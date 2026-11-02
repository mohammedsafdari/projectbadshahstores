"""Tests for the storefront search page (Slice 26)."""

from __future__ import annotations

import re

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.inventory.services import receive_stock


def _product_names(html: str) -> list[str]:
    """Names of the product cards on the page (each card has one <h2>)."""
    return re.findall(r"<h2[^>]*>\s*(.*?)\s*</h2>", html, re.S)


def _header_search_form(html: str) -> str:
    """The header search form only, so a test never reads the filter panel."""
    return html.split('<form role="search"', 1)[1].split("</form>", 1)[0]


def _visible_product(name_en: str, *, brand=None, category=None, is_active: bool = True):
    """Create a product with one active variant so it can appear on a page."""
    product = ProductFactory(
        name_en=name_en,
        brand=brand if brand is not None else BrandFactory(is_active=True),
        category=category if category is not None else CategoryFactory(is_active=True),
        is_active=is_active,
    )
    ProductVariantFactory(product=product, is_active=True)
    return product


@pytest.mark.django_db
def test_search_page_returns_200_and_matching_card(client: Client) -> None:
    _visible_product("Parle Biscuits")
    _visible_product("Amul Butter")
    response = client.get("/search/", {"q": "biscuit"})
    assert response.status_code == 200
    assert _product_names(response.content.decode()) == ["Parle Biscuits"]


@pytest.mark.django_db
def test_search_orders_best_match_first(client: Client) -> None:
    _visible_product("Nestle Milk")
    _visible_product("Nestle")
    response = client.get("/search/", {"q": "nestle"})
    assert _product_names(response.content.decode()) == ["Nestle", "Nestle Milk"]


@pytest.mark.django_db
def test_search_hides_inactive_product(client: Client) -> None:
    _visible_product("Visible Biscuit")
    _visible_product("Hidden Biscuit", is_active=False)
    response = client.get("/search/", {"q": "biscuit"})
    assert _product_names(response.content.decode()) == ["Visible Biscuit"]


@pytest.mark.django_db
def test_search_hides_product_with_inactive_brand(client: Client) -> None:
    _visible_product("Visible Biscuit")
    hidden_brand = BrandFactory(is_active=False, name="Closed Brand")
    _visible_product("Hidden Biscuit", brand=hidden_brand)
    response = client.get("/search/", {"q": "biscuit"})
    assert _product_names(response.content.decode()) == ["Visible Biscuit"]


@pytest.mark.django_db
def test_search_hides_product_in_hidden_category(client: Client) -> None:
    _visible_product("Visible Biscuit")
    hidden_category = CategoryFactory(is_active=False, name="Closed Category")
    _visible_product("Hidden Biscuit", category=hidden_category)
    response = client.get("/search/", {"q": "biscuit"})
    assert _product_names(response.content.decode()) == ["Visible Biscuit"]


@pytest.mark.django_db
def test_search_hides_product_without_active_variant(client: Client) -> None:
    _visible_product("Visible Biscuit")
    brand = BrandFactory(is_active=True)
    category = CategoryFactory(is_active=True)
    hidden = ProductFactory(
        name_en="Hidden Biscuit", brand=brand, category=category, is_active=True
    )
    ProductVariantFactory(product=hidden, is_active=False)
    response = client.get("/search/", {"q": "biscuit"})
    assert _product_names(response.content.decode()) == ["Visible Biscuit"]


@pytest.mark.django_db
def test_search_empty_query_shows_hint_without_error(client: Client) -> None:
    _visible_product("Biscuit")
    response = client.get("/search/")
    html = response.content.decode()
    assert response.status_code == 200
    assert "Type at least 2 letters." in html
    assert 'href="/products/"' in html
    assert _product_names(html) == []


@pytest.mark.django_db
def test_search_one_letter_query_shows_hint(client: Client) -> None:
    _visible_product("Aata")
    response = client.get("/search/", {"q": "a"})
    html = response.content.decode()
    assert response.status_code == 200
    assert "Type at least 2 letters." in html
    assert _product_names(html) == []


@pytest.mark.django_db
def test_search_no_results_shows_message_and_category_links(client: Client) -> None:
    CategoryFactory(is_active=True, name="Snacks", slug="snacks")
    CategoryFactory(is_active=True, name="Drinks", slug="drinks")
    response = client.get("/search/", {"q": "zzzz"})
    html = response.content.decode()
    assert "No products found for “zzzz”." in html
    assert "Check the spelling or try a shorter word." in html
    assert 'href="/products/"' in html
    assert 'href="/category/snacks/"' in html
    assert 'href="/category/drinks/"' in html


@pytest.mark.django_db
def test_search_escapes_query_in_page(client: Client) -> None:
    _visible_product("Biscuit")
    response = client.get("/search/", {"q": "<script>alert(1)</script>"})
    html = response.content.decode()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


@pytest.mark.django_db
def test_search_has_noindex_meta(client: Client) -> None:
    _visible_product("Biscuit")
    search_html = client.get("/search/", {"q": "biscuit"}).content.decode()
    listing_html = client.get("/products/").content.decode()
    assert '<meta name="robots" content="noindex">' in search_html
    assert '<meta name="robots" content="noindex">' not in listing_html


@pytest.mark.django_db
def test_search_applies_brand_filter(client: Client) -> None:
    alpha = BrandFactory(is_active=True, name="Alpha", slug="alpha")
    beta = BrandFactory(is_active=True, name="Beta", slug="beta")
    _visible_product("Alpha Biscuit", brand=alpha)
    _visible_product("Beta Biscuit", brand=beta)
    response = client.get("/search/", {"q": "biscuit", "brand": "alpha"})
    assert _product_names(response.content.decode()) == ["Alpha Biscuit"]


@pytest.mark.django_db
def test_search_applies_in_stock_filter(client: Client) -> None:
    stocked = _visible_product("Stocked Biscuit")
    _visible_product("Empty Biscuit")
    receive_stock(variant=stocked.variants.get(), quantity=5)
    response = client.get("/search/", {"q": "biscuit", "in_stock": "1"})
    assert _product_names(response.content.decode()) == ["Stocked Biscuit"]


@pytest.mark.django_db
def test_search_pagination_keeps_q_and_filters_in_links(client: Client) -> None:
    brand = BrandFactory(is_active=True, name="Alpha", slug="alpha")
    category = CategoryFactory(is_active=True)
    for number in range(30):
        product = ProductFactory(
            name_en=f"Match {number:02d}", brand=brand, category=category, is_active=True
        )
        ProductVariantFactory(product=product, is_active=True)
    response = client.get("/search/", {"q": "match", "brand": "alpha"})
    html = response.content.decode()
    match = re.search(r"<a[^>]*>Next</a>", html)
    assert match is not None
    link = match.group(0)
    assert "page=2" in link
    assert "q=match" in link
    assert "brand=alpha" in link


@pytest.mark.django_db
def test_search_page_2_has_the_rest(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    category = CategoryFactory(is_active=True)
    for number in range(1, 31):
        product = ProductFactory(
            name_en=f"Match {number:02d}", brand=brand, category=category, is_active=True
        )
        ProductVariantFactory(product=product, is_active=True)
    page_one = _product_names(client.get("/search/", {"q": "match"}).content.decode())
    page_two = _product_names(client.get("/search/", {"q": "match", "page": "2"}).content.decode())
    assert page_one == [f"Match {number:02d}" for number in range(1, 25)]
    assert page_two == [f"Match {number:02d}" for number in range(25, 31)]


@pytest.mark.django_db
def test_search_htmx_returns_only_results_block_with_vary_header(client: Client) -> None:
    _visible_product("Parle Biscuits")
    response = client.get("/search/", {"q": "biscuit"}, HTTP_HX_REQUEST="true")
    html = response.content.decode()
    assert response.status_code == 200
    assert "HX-Request" in response["Vary"]
    assert _product_names(html) == ["Parle Biscuits"]
    assert "<html" not in html
    assert 'id="listing-form"' not in html


@pytest.mark.django_db
def test_search_relevance_sort_offered_only_on_search_page(client: Client) -> None:
    _visible_product("Parle Biscuits")
    search_html = client.get("/search/", {"q": "biscuit"}).content.decode()
    listing_html = client.get("/products/").content.decode()
    relevance = re.search(r'<option value="relevance"[^>]*>', search_html)
    assert relevance is not None
    assert "selected" in relevance.group(0)
    assert 'value="relevance"' not in listing_html


@pytest.mark.django_db
def test_search_query_count_does_not_grow_with_results(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    category = CategoryFactory(is_active=True)
    for number in range(3):
        product = ProductFactory(
            name_en=f"Count Item {number}", brand=brand, category=category, is_active=True
        )
        ProductVariantFactory(product=product, is_active=True)
    with CaptureQueriesContext(connection) as small:
        response = client.get("/search/", {"q": "count"})
    assert response.status_code == 200
    assert _product_names(response.content.decode()) == [
        "Count Item 0",
        "Count Item 1",
        "Count Item 2",
    ]
    for number in range(3, 12):
        product = ProductFactory(
            name_en=f"Count Item {number}", brand=brand, category=category, is_active=True
        )
        ProductVariantFactory(product=product, is_active=True)
    with CaptureQueriesContext(connection) as large:
        response = client.get("/search/", {"q": "count"})
    assert response.status_code == 200
    assert len(_product_names(response.content.decode())) == 12
    assert len(large) == len(small)


@pytest.mark.django_db
def test_header_search_form_on_every_page(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    category = CategoryFactory(is_active=True, name="Snacks", slug="snacks")
    product = _visible_product("Parle Biscuits", brand=brand, category=category)
    urls = [
        "/",
        "/products/",
        "/search/",
        "/search/?q=biscuit",
        f"/category/{category.slug}/",
        f"/product/{product.slug}/",
    ]
    assert len(urls) == 6
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200
        form = _header_search_form(response.content.decode())
        assert 'action="/search/"' in form
        assert 'type="search"' in form
        assert 'name="q"' in form
        assert 'maxlength="100"' in form


@pytest.mark.django_db
def test_header_search_form_prefilled_on_search_page(client: Client) -> None:
    _visible_product("Nestle Milk")
    html = client.get("/search/", {"q": "Nestle"}).content.decode()
    form = _header_search_form(html)
    assert 'value="nestle"' in form
    other = client.get("/products/").content.decode()
    assert 'value="nestle"' not in _header_search_form(other)
