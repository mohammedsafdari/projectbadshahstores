"""Tests for the product search backend (Slice 26)."""

from __future__ import annotations

import pytest

from apps.catalog.search import (
    SEARCH_CANDIDATE_LIMIT,
    PostgresSearchBackend,
    SearchHit,
    build_prefix_tsquery,
    get_search_backend,
    normalise_query,
)
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)


def _make_product(
    name_en: str,
    *,
    brand_name: str | None = None,
    category_name: str | None = None,
    keywords: str = "",
    name_hi: str = "",
) -> int:
    """Create a visible-looking product and return its id."""
    brand = (
        BrandFactory(is_active=True, name=brand_name)
        if brand_name is not None
        else BrandFactory(is_active=True)
    )
    category = (
        CategoryFactory(is_active=True, name=category_name)
        if category_name is not None
        else CategoryFactory(is_active=True)
    )
    product = ProductFactory(
        name_en=name_en,
        name_hi=name_hi,
        search_keywords=keywords,
        brand=brand,
        category=category,
        is_active=True,
    )
    ProductVariantFactory(product=product, is_active=True)
    return product.pk


def _search_ids(query: str) -> list[int]:
    """The ids the default backend returns, best match first."""
    backend = PostgresSearchBackend()
    return [hit.product_id for hit in backend.search(query)]


class FakeSearchBackend:
    """A tiny backend used to prove the setting is read."""

    def search(self, query: str, limit: int = SEARCH_CANDIDATE_LIMIT) -> list[SearchHit]:
        return [SearchHit(product_id=1, score=1.0)]


def test_normalise_query_strips_and_lowercases() -> None:
    assert normalise_query("  Nestle  MILK ") == "nestle milk"


def test_normalise_query_collapses_spaces() -> None:
    assert normalise_query("nestle \t milk\n powder") == "nestle milk powder"


def test_normalise_query_cuts_to_100_characters() -> None:
    assert normalise_query("a" * 150) == "a" * 100


def test_normalise_query_keeps_six_words() -> None:
    assert normalise_query("one two three four five six seven") == "one two three four five six"


def test_normalise_query_one_letter_is_empty() -> None:
    assert normalise_query(" A ") == ""


def test_build_prefix_tsquery_makes_prefix_on_last_word() -> None:
    assert build_prefix_tsquery("nestle milk") == "nestle & milk:*"


def test_build_prefix_tsquery_removes_tsquery_syntax_characters() -> None:
    assert build_prefix_tsquery("ne&stle !milk:") == "nestle & milk:*"


def test_build_prefix_tsquery_keeps_devanagari_word() -> None:
    assert build_prefix_tsquery("आटा") == "आटा:*"


def test_build_prefix_tsquery_returns_none_when_nothing_left() -> None:
    assert build_prefix_tsquery("& | ! ( )") is None


@pytest.mark.django_db
def test_search_finds_by_name_en() -> None:
    product_id = _make_product("Parle-G Biscuits")
    assert _search_ids("parle") == [product_id]


@pytest.mark.django_db
def test_search_finds_by_prefix() -> None:
    product_id = _make_product("Nestle Milk")
    assert _search_ids("nes") == [product_id]


@pytest.mark.django_db
def test_search_finds_by_brand_name() -> None:
    product_id = _make_product("Everyday Milk Powder", brand_name="Nestle")
    assert _search_ids("nestle") == [product_id]


@pytest.mark.django_db
def test_search_finds_by_category_name() -> None:
    product_id = _make_product("KitKat", category_name="Chocolates")
    assert _search_ids("chocolates") == [product_id]


@pytest.mark.django_db
def test_search_finds_by_search_keywords_hinglish() -> None:
    product_id = _make_product("Whole Wheat Flour", keywords="aata atta wheat flour")
    assert _search_ids("aata") == [product_id]


@pytest.mark.django_db
def test_search_finds_by_name_hi_devanagari() -> None:
    product_id = _make_product("Whole Wheat Flour", name_hi="आटा")
    assert _search_ids("आटा") == [product_id]


@pytest.mark.django_db
def test_search_tolerates_one_letter_typo() -> None:
    product_id = _make_product("Biscuits")
    assert _search_ids("biscuts") == [product_id]


@pytest.mark.django_db
def test_search_exact_name_ranks_before_prefix_match() -> None:
    exact_id = _make_product("Nestle")
    prefix_id = _make_product("Nestle Milk")
    assert _search_ids("nestle") == [exact_id, prefix_id]


@pytest.mark.django_db
def test_search_prefix_match_ranks_before_trigram_only_match() -> None:
    prefix_id = _make_product("Nestell Choco")
    trigram_id = _make_product("Nestle Milk")
    assert _search_ids("nestel") == [prefix_id, trigram_id]


@pytest.mark.django_db
def test_search_requires_every_word() -> None:
    both_id = _make_product("Salt Crystals")
    _make_product("Salt")
    _make_product("Iodised Salt")
    assert _search_ids("salt crystals") == [both_id]


@pytest.mark.django_db
def test_search_syntax_characters_do_not_raise() -> None:
    product_id = _make_product("Nestle Milk")
    hits = PostgresSearchBackend().search("nestle & | ! ( ) : *")
    assert [hit.product_id for hit in hits] == [product_id]


@pytest.mark.django_db
def test_search_empty_query_returns_nothing() -> None:
    _make_product("Nestle Milk")
    backend = PostgresSearchBackend()
    assert backend.search("") == []
    assert backend.search("   ") == []
    assert backend.search("a") == []


@pytest.mark.django_db
def test_search_respects_candidate_limit() -> None:
    brand = BrandFactory(is_active=True)
    category = CategoryFactory(is_active=True)
    for number in range(205):
        ProductFactory(
            name_en=f"Bulk Item {number:03d}",
            brand=brand,
            category=category,
            is_active=True,
        )
    hits = PostgresSearchBackend().search("bulk")
    assert len(hits) == SEARCH_CANDIDATE_LIMIT


def test_get_search_backend_default_is_postgres() -> None:
    assert isinstance(get_search_backend(), PostgresSearchBackend)


def test_get_search_backend_uses_setting(settings) -> None:
    settings.SEARCH_BACKEND = "apps.catalog.tests.test_search.FakeSearchBackend"
    backend = get_search_backend()
    assert isinstance(backend, FakeSearchBackend)
    assert backend.search("anything") == [SearchHit(product_id=1, score=1.0)]
