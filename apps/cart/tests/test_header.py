"""Tests for the header cart count and the messages block (D61)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.accounts.views import CART_MERGE_MESSAGE
from apps.cart.stores import GUEST_CART_SESSION_KEY
from apps.cart.tests.factories import CartFactory, CartItemFactory
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductFactory, ProductVariantFactory
from apps.inventory.tests.factories import InventoryMovementFactory
from apps.notifications.sms import SmsBackend
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
SENT: list[tuple[str, str]] = []


class RecordingSmsBackend(SmsBackend):
    """Keep the codes in memory instead of printing or sending them."""

    def send_otp(self, phone: str, code: str) -> None:
        SENT.append((phone, code))


@pytest.fixture(autouse=True)
def _clear_settings_cache(settings):
    settings.SMS_BACKEND = f"{__name__}.RecordingSmsBackend"
    SENT.clear()
    cache.clear()
    yield
    cache.clear()
    SENT.clear()


def _variant(*, stock: str = "10.000", price: str = "100.00") -> ProductVariant:
    product = ProductFactory(is_active=True)
    variant = ProductVariantFactory(
        product=product,
        is_active=True,
        unit=ProductVariant.Unit.PACKET,
        pack_size=Decimal("1.000"),
        quantity_step=Decimal("1"),
        selling_price=Decimal(price),
        mrp=Decimal(price),
    )
    if Decimal(stock):
        InventoryMovementFactory(variant=variant, quantity_change=Decimal(stock))
    return variant


def _guest_with(client: Client, lines: list[tuple[ProductVariant, str]]) -> None:
    session = client.session
    session[GUEST_CART_SESSION_KEY] = {str(variant.pk): quantity for variant, quantity in lines}
    session.save()


def _cart_for(user, variants: list[ProductVariant]) -> None:
    cart = CartFactory(user=user)
    for variant in variants:
        CartItemFactory(cart=cart, variant=variant, quantity=Decimal("1.000"))


def test_header_cart_link_points_to_the_cart_page() -> None:
    html = Client().get(reverse("home")).content.decode()

    assert 'href="/cart/"' in html


def test_header_shows_no_badge_for_an_empty_cart() -> None:
    html = Client().get(reverse("home")).content.decode()

    assert 'id="cart-count"' in html
    assert 'data-count="0"' in html


def test_header_badge_shows_the_line_count_for_a_guest() -> None:
    first = _variant()
    second = _variant()
    client = Client()
    _guest_with(client, [(first, "1.000"), (second, "2.000")])

    html = client.get(reverse("home")).content.decode()

    assert 'data-count="2"' in html


def test_header_badge_shows_the_line_count_for_a_customer() -> None:
    user = UserFactory()
    _cart_for(user, [_variant(), _variant(), _variant()])
    client = Client()
    client.force_login(user)

    html = client.get(reverse("home")).content.decode()

    assert 'data-count="3"' in html


def test_messages_are_shown_on_the_next_page() -> None:
    variant = _variant()
    client = Client()

    response = client.post(
        reverse("cart_add"),
        {"variant_id": str(variant.pk), "quantity": "1"},
    )
    html = client.get(response["Location"]).content.decode()

    assert "Added 1 pack." in html


def test_messages_block_is_a_polite_live_region() -> None:
    variant = _variant()
    client = Client()

    response = client.post(
        reverse("cart_add"),
        {"variant_id": str(variant.pk), "quantity": "1"},
    )
    html = client.get(response["Location"]).content.decode()

    assert 'role="status"' in html
    assert 'aria-live="polite"' in html


def test_login_merge_message_is_visible_after_login() -> None:
    variant = _variant(stock="1.000")
    client = Client()
    _guest_with(client, [(variant, "2.000")])
    client.post(reverse("account_login"), {"phone": PHONE})

    code = SENT[-1][1]
    response = client.post(reverse("account_verify"), {"code": code}, follow=True)

    assert response.status_code == 200
    assert str(CART_MERGE_MESSAGE) in response.content.decode()
