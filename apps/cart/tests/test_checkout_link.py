"""Tests for the cart's checkout link (D66)."""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.cart.stores import GUEST_CART_SESSION_KEY
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.core.models import ShopSettings
from apps.inventory.tests.factories import InventoryMovementFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before every test."""
    cache.clear()
    ShopSettings.load()
    yield
    cache.clear()


def _set_settings(
    delivery_fee: str = "45.00",
    threshold: str = "800.00",
    minimum: str = "150.00",
) -> None:
    row = ShopSettings.load()
    row.delivery_fee = Decimal(delivery_fee)
    row.free_delivery_threshold = Decimal(threshold)
    row.minimum_order_value = Decimal(minimum)
    row.save(
        update_fields=[
            "delivery_fee",
            "free_delivery_threshold",
            "minimum_order_value",
            "updated_at",
        ]
    )


def _variant(*, stock: str = "10.000", price: str = "100.00") -> ProductVariant:
    variant = ProductVariantFactory(
        unit=ProductVariant.Unit.PACKET,
        pack_size=Decimal("1.000"),
        quantity_step=Decimal("1"),
        selling_price=Decimal(price),
        mrp=Decimal(price),
    )
    InventoryMovementFactory(variant=variant, quantity_change=Decimal(stock))
    return variant


def _guest_with(client: Client, lines: list[tuple[ProductVariant, str]]) -> None:
    session = client.session
    session[GUEST_CART_SESSION_KEY] = {str(variant.pk): quantity for variant, quantity in lines}
    session.save()


def _checkout_links(html: str) -> list[str]:
    """Every checkout anchor, as a whole tag, so an unrelated URL cannot pass."""
    return re.findall(r"<a\b[^>]*\bdata-checkout-link\b[^>]*>", html)


def test_cart_page_links_to_checkout_when_ready() -> None:
    _set_settings()
    client = Client()
    _guest_with(client, [(_variant(), "2.000")])

    html = client.get(reverse("cart_detail")).content.decode()

    links = _checkout_links(html)
    assert len(links) == 1
    assert 'href="/checkout/"' in links[0]


def test_cart_page_has_no_checkout_link_for_a_cart_below_the_minimum() -> None:
    _set_settings()
    client = Client()
    _guest_with(client, [(_variant(), "1.000")])

    html = client.get(reverse("cart_detail")).content.decode()

    assert _checkout_links(html) == []


def test_cart_page_has_no_checkout_link_for_an_empty_cart() -> None:
    _set_settings()
    client = Client()

    html = client.get(reverse("cart_detail")).content.decode()

    assert _checkout_links(html) == []
