"""Tests for the search suggestion fragment (Slice 27a)."""

from __future__ import annotations

import re

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils.html import escape

from apps.catalog.search import SearchHit
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)


def _suggestion_names(html: str) -> list[str]:
    """Names of the suggested products (each suggestion has one <h2>)."""
    return re.findall(r"<h2[^>]*>\s*(.*?)\s*</h2>", html, re.S)


def _visible_product(name_en: str, *, brand=None, category=None, is_active: bool = True):
    """Create a product with one active variant so it can appear in the list."""
    product = ProductFactory(
        name_en=name_en,
        brand=brand if brand is not None else BrandFactory(is_active=True),
        category=category if category is not None else CategoryFactory(is_active=True),
        is_active=is_active,
    )
    ProductVariantFactory(product=product, is_active=True)
    return product


@pytest.mark.django_db
def test_suggest_returns_200_and_matching_product_link(client: Client) -> None:
    product = _visible_product("Parle Biscuits")
    _visible_product("Amul Butter")
    response = client.get("/search/suggest/", {"q": "biscuit"})
    html = response.content.decode()
    assert response.status_code == 200
    assert _suggestion_names(html) == ["Parle Biscuits"]
    assert f'href="/product/{product.slug}/"' in html


@pytest.mark.django_db
def test_suggest_orders_best_match_first(client: Client) -> None:
    _visible_product("Amul Kool")
    _visible_product("Kool")
    response = client.get("/search/suggest/", {"q": "kool"})
    names = _suggestion_names(response.content.decode())
    assert names
    # Alphabetically "Amul Kool" comes first, so this order proves the ranking.
    assert names == ["Kool", "Amul Kool"]


@pytest.mark.django_db
def test_suggest_returns_at_most_six_products(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    category = CategoryFactory(is_active=True)
    for number in range(8):
        product = ProductFactory(
            name_en=f"Suggest Item {number}", brand=brand, category=category, is_active=True
        )
        ProductVariantFactory(product=product, is_active=True)
    response = client.get("/search/suggest/", {"q": "suggest"})
    names = _suggestion_names(response.content.decode())
    assert names
    assert len(names) == 6
    assert names == sorted(names)


@pytest.mark.django_db
def test_suggest_hides_inactive_product(client: Client) -> None:
    _visible_product("Visible Biscuit")
    _visible_product("Hidden Biscuit", is_active=False)
    response = client.get("/search/suggest/", {"q": "biscuit"})
    assert _suggestion_names(response.content.decode()) == ["Visible Biscuit"]


@pytest.mark.django_db
def test_suggest_hides_product_with_inactive_brand(client: Client) -> None:
    _visible_product("Visible Biscuit")
    hidden_brand = BrandFactory(is_active=False, name="Closed Brand")
    _visible_product("Hidden Biscuit", brand=hidden_brand)
    response = client.get("/search/suggest/", {"q": "biscuit"})
    assert _suggestion_names(response.content.decode()) == ["Visible Biscuit"]


@pytest.mark.django_db
def test_suggest_hides_product_in_hidden_category(client: Client) -> None:
    _visible_product("Visible Biscuit")
    hidden_category = CategoryFactory(is_active=False, name="Closed Category")
    _visible_product("Hidden Biscuit", category=hidden_category)
    response = client.get("/search/suggest/", {"q": "biscuit"})
    assert _suggestion_names(response.content.decode()) == ["Visible Biscuit"]


@pytest.mark.django_db
def test_suggest_hides_product_without_active_variant(client: Client) -> None:
    _visible_product("Visible Biscuit")
    brand = BrandFactory(is_active=True)
    category = CategoryFactory(is_active=True)
    hidden = ProductFactory(
        name_en="Hidden Biscuit",
        brand=brand,
        category=category,
        is_active=True,
    )
    ProductVariantFactory(product=hidden, is_active=False)
    response = client.get("/search/suggest/", {"q": "biscuit"})
    assert _suggestion_names(response.content.decode()) == ["Visible Biscuit"]


@pytest.mark.django_db
def test_suggest_empty_query_returns_empty_fragment_without_searching(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class NeverCalledBackend:
        def search(self, query: str, limit: int = 50) -> list[SearchHit]:
            calls.append(query)
            return []

    # The view imports the helper by name, so patch it there, not in the module
    # that defines it.
    monkeypatch.setattr("apps.storefront.views.get_search_backend", lambda: NeverCalledBackend())
    _visible_product("Biscuit")
    response = client.get("/search/suggest/")
    assert response.status_code == 200
    assert response.content == b""
    assert calls == []


@pytest.mark.django_db
def test_suggest_one_letter_query_returns_empty_fragment(client: Client) -> None:
    _visible_product("Aata")
    response = client.get("/search/suggest/", {"q": "a"})
    assert response.status_code == 200
    assert response.content == b""


@pytest.mark.django_db
def test_suggest_no_results_shows_message_and_no_see_all_link(client: Client) -> None:
    _visible_product("Biscuit")
    response = client.get("/search/suggest/", {"q": "zzzz"})
    html = response.content.decode()
    assert "No products found for “zzzz”." in html
    assert "See all results" not in html
    assert 'id="search-suggestions-list"' not in html
    assert _suggestion_names(html) == []


@pytest.mark.django_db
def test_suggest_shows_see_all_link_with_urlencoded_query(client: Client) -> None:
    _visible_product("Atta & Dal Mix")
    response = client.get("/search/suggest/", {"q": "Atta & Dal"})
    html = response.content.decode()
    assert _suggestion_names(html) == [escape("Atta & Dal Mix")]
    # Django's urlencode filter quotes a space as %20, not +.
    assert 'href="/search/?q=atta%20%26%20dal"' in html


@pytest.mark.django_db
def test_suggest_escapes_query_in_fragment(client: Client) -> None:
    _visible_product("Biscuit")
    response = client.get("/search/suggest/", {"q": "<script>x</script>"})
    html = response.content.decode()
    assert "<script>" not in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html


@pytest.mark.django_db
def test_suggest_marks_out_of_stock_product(client: Client) -> None:
    _visible_product("Biscuit")
    response = client.get("/search/suggest/", {"q": "biscuit"})
    html = response.content.decode()
    assert _suggestion_names(html) == ["Biscuit"]
    assert "Out of stock" in html


@pytest.mark.django_db
def test_suggest_matches_hindi_word(client: Client) -> None:
    # Only name_hi holds the Devanagari word: the English name, the brand, the
    # category and the keywords are plain English, so no other rule can match.
    brand = BrandFactory(is_active=True, name="Annapurna")
    category = CategoryFactory(is_active=True, name="Flours")
    product = ProductFactory(
        name_en="Chakki Atta",
        name_hi="आटा",
        search_keywords="",
        brand=brand,
        category=category,
        is_active=True,
    )
    ProductVariantFactory(product=product, is_active=True)
    _visible_product("Toor Dal")
    response = client.get("/search/suggest/", {"q": "आटा"})
    assert _suggestion_names(response.content.decode()) == ["Chakki Atta"]


@pytest.mark.django_db
def test_suggest_query_count_does_not_grow_with_hits(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    category = CategoryFactory(is_active=True)
    for number in range(3):
        product = ProductFactory(
            name_en=f"Count Item {number}", brand=brand, category=category, is_active=True
        )
        ProductVariantFactory(product=product, is_active=True)
    # The context manager records every query the request runs.
    with CaptureQueriesContext(connection) as small:
        response = client.get("/search/suggest/", {"q": "count"})
    assert response.status_code == 200
    small_names = _suggestion_names(response.content.decode())
    assert small_names == ["Count Item 0", "Count Item 1", "Count Item 2"]
    for number in range(3, 12):
        product = ProductFactory(
            name_en=f"Count Item {number}", brand=brand, category=category, is_active=True
        )
        ProductVariantFactory(product=product, is_active=True)
    with CaptureQueriesContext(connection) as large:
        response = client.get("/search/suggest/", {"q": "count"})
    assert response.status_code == 200
    assert len(_suggestion_names(response.content.decode())) == 6
    assert len(large) == len(small)


@pytest.mark.django_db
def test_suggest_sends_noindex_and_no_store_headers(client: Client) -> None:
    _visible_product("Biscuit")
    response = client.get("/search/suggest/", {"q": "biscuit"})
    assert response["X-Robots-Tag"] == "noindex"
    # never_cache may add other words, so only look for no-store.
    assert "no-store" in response["Cache-Control"]


@pytest.mark.django_db
def test_suggest_rejects_post_with_405(client: Client) -> None:
    response = client.post("/search/suggest/", {"q": "biscuit"})
    assert response.status_code == 405
