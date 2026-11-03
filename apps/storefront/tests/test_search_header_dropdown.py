"""Tests for the header search-as-you-type dropdown (Slice 27b)."""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)

# One tag's attributes, e.g. {"id": "header-search", "name": "q"}.
Attributes = dict[str, str | None]


class _HeaderParser(HTMLParser):
    """Collect the header search form and the tags inside it, one tag at a time."""

    def __init__(self) -> None:
        super().__init__()
        self.form: Attributes | None = None
        self.input: Attributes | None = None
        self.panel: Attributes | None = None
        self.submit: Attributes | None = None
        self._in_form = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form" and attributes.get("role") == "search":
            self.form = attributes
            self._in_form = True
        elif self._in_form and tag == "input" and attributes.get("id") == "header-search":
            self.input = attributes
        elif self._in_form and tag == "div" and attributes.get("id") == "search-suggestions":
            self.panel = attributes
        elif self._in_form and tag == "button" and attributes.get("type") == "submit":
            self.submit = attributes

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._in_form = False


def _header(html: str) -> _HeaderParser:
    parser = _HeaderParser()
    parser.feed(html)
    return parser


def _visible_product(name_en: str):
    """Create a product with one active variant so its detail page renders."""
    product = ProductFactory(
        name_en=name_en,
        brand=BrandFactory(is_active=True),
        category=CategoryFactory(is_active=True),
        is_active=True,
    )
    ProductVariantFactory(product=product, is_active=True)
    return product


@pytest.mark.django_db
def test_header_input_has_htmx_suggest_attributes(client: Client) -> None:
    field = _header(client.get("/").content.decode()).input
    assert field is not None
    assert field["hx-get"] == reverse("search_suggest")
    assert field["hx-target"] == "#search-suggestions"
    assert field["hx-swap"] == "innerHTML"
    assert field["hx-trigger"] is not None
    assert "delay:250ms" in field["hx-trigger"]
    assert field["hx-sync"] == "this:replace"
    assert field["hx-params"] == "q"
    assert field["hx-push-url"] == "false"


@pytest.mark.django_db
def test_header_input_keeps_plain_form_fields(client: Client) -> None:
    header = _header(client.get("/").content.decode())
    assert header.form is not None
    assert header.form["action"] == reverse("product_search")
    assert header.form["method"] == "get"
    assert header.input is not None
    assert header.input["id"] == "header-search"
    assert header.input["type"] == "search"
    assert header.input["name"] == "q"
    assert header.input["maxlength"] == "100"


@pytest.mark.django_db
def test_header_input_has_combobox_aria_attributes(client: Client) -> None:
    field = _header(client.get("/").content.decode()).input
    assert field is not None
    assert field["autocomplete"] == "off"
    assert field["role"] == "combobox"
    assert field["aria-autocomplete"] == "list"
    assert field["aria-controls"] == "search-suggestions-list"
    assert field[":aria-expanded"] == "open"


@pytest.mark.django_db
def test_header_suggestion_panel_exists_and_is_hidden_by_default(client: Client) -> None:
    panel = _header(client.get("/").content.decode()).panel
    assert panel is not None
    assert panel["id"] == "search-suggestions"
    assert panel["x-show"] == "open"
    assert "x-cloak" in panel
    assert panel["aria-live"] == "polite"


@pytest.mark.django_db
def test_header_panel_target_matches_hx_target(client: Client) -> None:
    header = _header(client.get("/").content.decode())
    assert header.input is not None
    assert header.panel is not None
    assert header.input["hx-target"] == "#" + header.panel["id"]


@pytest.mark.django_db
def test_header_dropdown_present_on_search_page_and_prefilled(client: Client) -> None:
    header = _header(client.get("/search/", {"q": "Biscuit"}).content.decode())
    assert header.form is not None
    assert header.form["x-data"] == "{ open: false }"
    assert header.input is not None
    assert header.input["value"] == "biscuit"
    assert header.panel is not None
    assert header.panel["x-show"] == "open"


@pytest.mark.django_db
def test_header_dropdown_present_on_product_detail_page(client: Client) -> None:
    product = _visible_product("Parle Biscuits")
    header = _header(client.get(f"/product/{product.slug}/").content.decode())
    assert header.input is not None
    assert header.input["hx-get"] == reverse("search_suggest")
    assert header.panel is not None
    assert header.panel["id"] == "search-suggestions"


@pytest.mark.django_db
def test_header_search_form_still_has_submit_button(client: Client) -> None:
    submit = _header(client.get("/").content.decode()).submit
    assert submit is not None
    assert submit["type"] == "submit"
