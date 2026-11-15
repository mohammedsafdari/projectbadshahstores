"""The Hindi product name on the pages a customer reads (D92).

Only the visible name follows the visitor's language. Page titles, the meta
description, Open Graph, JSON-LD, breadcrumbs and alt text keep the English
name, and the Hindi name costs no extra query because it is on the row the page
already reads.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal

import pytest
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.cart.tests.factories import CartFactory, CartItemFactory
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.core.models import ShopSettings
from apps.inventory.tests.factories import InventoryMovementFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

LANGUAGE_COOKIE = settings.LANGUAGE_COOKIE_NAME
BOTH_LANGUAGES = [("en-IN", "English"), ("hi", "हिन्दी")]
NAME_EN = "Milk Powder"
NAME_HI = "दूध पाउडर"


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """One settings row and an empty cache, so no test sees a stale cache."""
    cache.clear()
    ShopSettings.load()
    yield
    cache.clear()


@pytest.fixture
def hindi_on(settings):
    """The switch on and both languages offered, as a production run has it."""
    settings.HINDI_ENABLED = True
    settings.LANGUAGES = BOTH_LANGUAGES
    return settings


@pytest.fixture
def hindi_off(settings):
    """The switch off, whatever a developer's own .env says."""
    settings.HINDI_ENABLED = False
    settings.LANGUAGES = [("en-IN", "English")]
    return settings


def _product(*, name_hi: str = NAME_HI):
    """One visible product with one packet variant that is in stock."""
    brand = BrandFactory(is_active=True)
    category = CategoryFactory(is_active=True)
    product = ProductFactory(
        name_en=NAME_EN,
        name_hi=name_hi,
        brand=brand,
        category=category,
        is_active=True,
    )
    variant = ProductVariantFactory(
        product=product,
        is_active=True,
        pack_size=Decimal("1.000"),
        selling_price=Decimal("100.00"),
        mrp=Decimal("120.00"),
    )
    InventoryMovementFactory(variant=variant, quantity_change=Decimal("10.000"))
    return product, variant


def _hindi_visitor(client: Client) -> None:
    """Send the next request with the Hindi cookie."""
    client.cookies[LANGUAGE_COOKIE] = "hi"


# ---------------------------------------------------------------------------
# A card, the suggestion box and the detail page
# ---------------------------------------------------------------------------
def test_the_card_shows_the_hindi_name_for_a_hindi_visitor(hindi_on, client: Client) -> None:
    _product()
    _hindi_visitor(client)

    page = client.get(reverse("product_list")).content.decode()

    assert NAME_HI in page
    # The alt text stays the English name, so a screen reader keeps one name.
    assert f'alt="{NAME_EN}"' in page


def test_the_card_shows_english_for_an_english_visitor(hindi_on, client: Client) -> None:
    _product()

    page = client.get(reverse("product_list")).content.decode()

    assert NAME_EN in page
    assert NAME_HI not in page


def test_the_detail_heading_shows_the_hindi_name(hindi_on, client: Client) -> None:
    product, _variant = _product()
    _hindi_visitor(client)

    page = client.get(reverse("product_detail", args=[product.slug])).content.decode()

    assert f'<h1 class="text-2xl font-bold text-gray-900">{NAME_HI}</h1>' in page


def test_the_detail_title_og_title_and_json_ld_stay_english_for_a_hindi_visitor(
    hindi_on, client: Client
) -> None:
    product, _variant = _product()
    _hindi_visitor(client)

    page = client.get(reverse("product_detail", args=[product.slug])).content.decode()

    title = re.search(r"<title>(.*?)</title>", page)
    assert title is not None
    assert NAME_EN in title.group(1)
    assert NAME_HI not in title.group(1)
    assert f'property="og:title" content="{NAME_EN} — ' in page
    # json.loads turns each block back into a dict, so the product's own name
    # is checked and not the breadcrumb, which prints the same English name.
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.DOTALL)
    data = [json.loads(block) for block in blocks]
    product_data = [item for item in data if item.get("@type") == "Product"]
    assert product_data
    assert product_data[0]["name"] == NAME_EN


def test_the_breadcrumb_stays_english_for_a_hindi_visitor(hindi_on, client: Client) -> None:
    product, _variant = _product()
    _hindi_visitor(client)

    page = client.get(reverse("product_detail", args=[product.slug])).content.decode()

    # re.search takes the breadcrumb element, so the h1 cannot satisfy this.
    nav = re.search(r'<nav aria-label="Breadcrumb".*?</nav>', page, re.DOTALL)
    assert nav is not None
    assert NAME_EN in nav.group(0)
    assert NAME_HI not in nav.group(0)


def test_the_search_suggestions_show_the_hindi_name(hindi_on, client: Client) -> None:
    _product()
    _hindi_visitor(client)

    page = client.get(reverse("search_suggest"), {"q": "milk"}).content.decode()

    assert NAME_HI in page


# ---------------------------------------------------------------------------
# The cart and the fallbacks
# ---------------------------------------------------------------------------
def test_the_cart_line_shows_the_hindi_name(hindi_on, client: Client) -> None:
    _product_row, variant = _product()
    user = UserFactory()
    cart = CartFactory(user=user)
    CartItemFactory(cart=cart, variant=variant, quantity=Decimal("1.000"))
    client.force_login(user)
    _hindi_visitor(client)

    page = client.get(reverse("cart_detail")).content.decode()

    assert NAME_HI in page


def test_a_product_without_a_hindi_name_shows_english_to_a_hindi_visitor(
    hindi_on, client: Client
) -> None:
    product, _variant = _product(name_hi="")
    _hindi_visitor(client)

    page = client.get(reverse("product_detail", args=[product.slug])).content.decode()

    assert f'<h1 class="text-2xl font-bold text-gray-900">{NAME_EN}</h1>' in page


def test_a_hindi_cookie_changes_no_name_while_hindi_is_off(hindi_off, client: Client) -> None:
    _product()
    _hindi_visitor(client)

    page = client.get(reverse("product_list")).content.decode()

    assert NAME_EN in page
    assert NAME_HI not in page


def test_the_hindi_name_page_costs_no_extra_query(hindi_on, client: Client) -> None:
    _product()
    # The first request fills the shop settings cache, so the two measured
    # requests start from the same place.
    client.get(reverse("product_list"))
    cache.clear()
    with CaptureQueriesContext(connection) as english:
        client.get(reverse("product_list"))
    _hindi_visitor(client)
    cache.clear()
    with CaptureQueriesContext(connection) as hindi:
        client.get(reverse("product_list"))

    assert len(hindi) == len(english)
