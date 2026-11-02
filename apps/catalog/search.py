"""Product search behind a small interface (Slice 26, D26, D53).

The backend only finds candidate product ids. Visibility, stock and prices are
the storefront's job, so an embedding backend can replace this class later
without knowing anything about the shop rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, cast

from django.conf import settings
from django.contrib.postgres.search import (
    SearchQuery,
    SearchRank,
    SearchVector,
    TrigramWordSimilarity,
)
from django.db.models import Case, F, FloatField, Q, Value, When
from django.db.models.functions import Lower
from django.utils.module_loading import import_string

from apps.catalog.models import Product

# At most this many ids leave the backend; the storefront intersects them with
# the visible products and paginates the result.
SEARCH_CANDIDATE_LIMIT = 200

# A word-similarity score at or above this counts as a typo-tolerant match.
SIMILARITY_THRESHOLD = 0.4

MAX_QUERY_LENGTH = 100
MAX_QUERY_WORDS = 6
MIN_QUERY_LENGTH = 2

# PostgreSQL's ts_rank returns a tiny value such as 1e-20 for a document that
# does not match, so a full-text match is "rank above this small epsilon".
MIN_SEARCH_RANK = 1e-6

# Score bands: exact name beats a name prefix, which beats any other full-text
# match, which beats an icontains-only or trigram-only match.
SCORE_EXACT_NAME = 3.0
SCORE_NAME_PREFIX = 2.5
SCORE_FULL_TEXT_BASE = 1.0
SCORE_FULL_TEXT_RANK = 0.5
SCORE_WEAK_MATCH = 0.5

DEFAULT_SEARCH_BACKEND = "apps.catalog.search.PostgresSearchBackend"

# Characters that mean something inside a PostgreSQL tsquery string.
TSQUERY_SYNTAX_CHARACTERS = frozenset("&|!():*'\"\\<>")


@dataclass(frozen=True)
class SearchHit:
    """One matching product and its relevance score (higher is better)."""

    product_id: int
    score: float


class SearchBackend(Protocol):
    """What the storefront needs from a search engine."""

    def search(self, query: str, limit: int = SEARCH_CANDIDATE_LIMIT) -> list[SearchHit]: ...


def normalise_query(text: str) -> str:
    """Lower-case, collapse spaces, cap the length and keep six words."""
    # split() with no argument splits on any whitespace and drops empty pieces,
    # so this also strips the ends and collapses repeated spaces.
    words = text.lower().split()
    shortened = " ".join(words)[:MAX_QUERY_LENGTH]
    normalised = " ".join(shortened.split()[:MAX_QUERY_WORDS])
    if len(normalised) < MIN_QUERY_LENGTH:
        return ""
    return normalised


def build_prefix_tsquery(query: str) -> str | None:
    """Turn words into a tsquery whose last word is a prefix match."""
    words: list[str] = []
    # Split on whitespace only. A \w regex would be wrong here: Python's \w
    # does not match Devanagari vowel signs such as the one in "आटा".
    for word in query.split():
        cleaned = "".join(char for char in word if char not in TSQUERY_SYNTAX_CHARACTERS)
        if cleaned:
            words.append(cleaned)
    if not words:
        return None
    # "aata & atta:*" means every word must match and the last one may be a
    # prefix, so "nes" finds "nestle".
    return " & ".join([*words[:-1], words[-1] + ":*"])


class PostgresSearchBackend:
    """PostgreSQL full-text search plus trigram word similarity."""

    def search(self, query: str, limit: int = SEARCH_CANDIDATE_LIMIT) -> list[SearchHit]:
        text = normalise_query(query)
        if not text:
            return []
        tsquery = build_prefix_tsquery(text)
        if tsquery is None:
            return []
        limit = min(limit, SEARCH_CANDIDATE_LIMIT)

        # One weighted vector over every field a customer might type. The
        # 'simple' configuration does no stemming and no stop words, which
        # suits brand names and Hinglish spellings better than 'english'.
        vector = (
            SearchVector("name_en", weight="A", config="simple")
            + SearchVector("name_hi", weight="B", config="simple")
            + SearchVector("brand__name", weight="B", config="simple")
            + SearchVector("category__name", weight="C", config="simple")
            + SearchVector("search_keywords", weight="B", config="simple")
        )
        search_query = SearchQuery(tsquery, search_type="raw", config="simple")
        # TrigramWordSimilarity(query, field) is WORD_SIMILARITY(query, field),
        # which compares the typed words with the words in the field.
        qs = Product.objects.annotate(
            search_rank=SearchRank(vector, search_query, normalization=32),
            name_similarity=TrigramWordSimilarity(text, "name_en"),
            brand_similarity=TrigramWordSimilarity(text, "brand__name"),
            keyword_similarity=TrigramWordSimilarity(text, "search_keywords"),
        )
        qs = qs.filter(
            Q(search_rank__gt=MIN_SEARCH_RANK)
            | Q(name_en__icontains=text)
            | Q(name_hi__icontains=text)
            | Q(search_keywords__icontains=text)
            | Q(name_similarity__gte=SIMILARITY_THRESHOLD)
            | Q(brand_similarity__gte=SIMILARITY_THRESHOLD)
            | Q(keyword_similarity__gte=SIMILARITY_THRESHOLD)
        )
        score = Case(
            When(name_en__iexact=text, then=Value(SCORE_EXACT_NAME)),
            When(name_en__istartswith=text, then=Value(SCORE_NAME_PREFIX)),
            When(
                search_rank__gt=MIN_SEARCH_RANK,
                then=Value(SCORE_FULL_TEXT_BASE) + F("search_rank") * SCORE_FULL_TEXT_RANK,
            ),
            default=Value(SCORE_WEAK_MATCH),
            output_field=FloatField(),
        )
        # Ties go to the name and then the pk, so the order never depends on
        # how PostgreSQL happens to return equal rows.
        ranked = qs.annotate(score=score).order_by("-score", Lower("name_en"), "pk")[:limit]
        # An annotation is invisible to mypy, so read the score through Any.
        return [SearchHit(product_id=row.pk, score=float(cast(Any, row).score)) for row in ranked]


def get_search_backend() -> SearchBackend:
    """Build the backend named in settings, or the PostgreSQL one by default."""
    # getattr keeps the default when the setting is absent, so no settings
    # file has to change for this slice.
    path = getattr(settings, "SEARCH_BACKEND", DEFAULT_SEARCH_BACKEND)
    backend_class = import_string(path)
    # A Protocol is structural: the cast only helps mypy, nothing is checked
    # at run time.
    return cast(SearchBackend, backend_class())
