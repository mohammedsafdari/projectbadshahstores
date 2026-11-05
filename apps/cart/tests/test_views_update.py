"""Tests for cart update and remove (D61)."""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.cart.models import CartItem
from apps.cart.stores import GUEST_CART_SESSION_KEY
from apps.cart.tests.factories import CartFactory, CartItemFactory
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductFactory, ProductVariantFactory
from apps.inventory.tests.factories import InventoryMovementFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    cache.clear()
    yield
    cache.clear()


def _variant(
    *, stock: str = "10.000", price: str = "100.00", loose: bool = False
) -> ProductVariant:
    product = ProductFactory(is_active=True)
    variant = ProductVariantFactory(
        product=product,
        is_active=True,
        is_sold_loose=loose,
        unit=ProductVariant.Unit.KG if loose else ProductVariant.Unit.PACKET,
        pack_size=Decimal("1.000"),
        quantity_step=Decimal("0.001") if loose else Decimal("1"),
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


def _db_cart(user, lines: list[tuple[ProductVariant, str]]) -> None:
    cart = CartFactory(user=user)
    for variant, quantity in lines:
        CartItemFactory(cart=cart, variant=variant, quantity=Decimal(quantity))


def _update(client: Client, variant: ProductVariant, quantity: str, **extra):
    data = {"variant_id": str(variant.pk), "quantity": quantity}
    # HTTP_* keyword arguments are headers; the rest are form fields.
    headers = {key: extra.pop(key) for key in list(extra) if key.startswith("HTTP_")}
    data.update(extra)
    return client.post(reverse("cart_update"), data, **headers)


def _remove(client: Client, variant: ProductVariant, **extra):
    data = {"variant_id": str(variant.pk)}
    headers = {key: extra.pop(key) for key in list(extra) if key.startswith("HTTP_")}
    data.update(extra)
    return client.post(reverse("cart_remove"), data, **headers)


def test_update_requires_post() -> None:
    assert Client().get(reverse("cart_update")).status_code == 405


def test_update_without_csrf_token_is_refused() -> None:
    variant = _variant()
    client = Client(enforce_csrf_checks=True)

    assert _update(client, variant, "2").status_code == 403


def test_update_replaces_the_quantity_for_a_guest() -> None:
    variant = _variant()
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    _update(client, variant, "3")

    assert client.session[GUEST_CART_SESSION_KEY] == {str(variant.pk): "3.000"}


def test_update_replaces_the_quantity_for_a_logged_in_customer() -> None:
    variant = _variant()
    user = UserFactory()
    _db_cart(user, [(variant, "1.000")])
    client = Client()
    client.force_login(user)

    _update(client, variant, "3")

    assert CartItem.objects.get(cart__user=user).quantity == Decimal("3.000")


def test_update_to_zero_removes_the_line() -> None:
    variant = _variant()
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    _update(client, variant, "0")

    assert client.session[GUEST_CART_SESSION_KEY] == {}


def test_htmx_update_returns_the_cart_body_fragment_only() -> None:
    variant = _variant()
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    response = _update(client, variant, "2", HTTP_HX_REQUEST="true")
    html = response.content.decode()

    assert response.status_code == 200
    assert html.startswith('<div id="cart-body"')
    assert "<html" not in html


def test_htmx_update_refreshes_the_header_count_out_of_band() -> None:
    variant = _variant()
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    html = _update(client, variant, "2", HTTP_HX_REQUEST="true").content.decode()

    assert 'id="cart-count"' in html
    assert 'hx-swap-oob="true"' in html


def test_htmx_update_error_keeps_the_old_quantity_and_shows_an_alert() -> None:
    variant = _variant(stock="2.000")
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    html = _update(client, variant, "5", HTTP_HX_REQUEST="true").content.decode()

    assert 'role="alert"' in html
    assert re.search(r'id="cart-quantity-\d+"[^>]*value="1"', html)


def test_update_over_available_is_refused_with_the_available_amount() -> None:
    variant = _variant(stock="2.000")
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    html = _update(client, variant, "5", HTTP_HX_REQUEST="true").content.decode()

    assert "2 packs" in html


def test_update_may_lower_a_flagged_line() -> None:
    variant = _variant(stock="2.000")
    client = Client()
    _guest_with(client, [(variant, "5.000")])

    html = _update(client, variant, "1", HTTP_HX_REQUEST="true").content.decode()

    assert 'role="alert"' not in html
    assert client.session[GUEST_CART_SESSION_KEY] == {str(variant.pk): "1.000"}


def test_non_htmx_update_redirects_to_the_cart() -> None:
    variant = _variant()
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    response = _update(client, variant, "2")

    assert response.status_code == 302
    assert response["Location"] == reverse("cart_detail")


def test_remove_requires_post() -> None:
    assert Client().get(reverse("cart_remove")).status_code == 405


def test_remove_deletes_the_line_for_a_guest() -> None:
    first = _variant()
    second = _variant()
    client = Client()
    _guest_with(client, [(first, "1.000"), (second, "2.000")])

    _remove(client, first)

    assert client.session[GUEST_CART_SESSION_KEY] == {str(second.pk): "2.000"}


def test_remove_deletes_the_line_for_a_logged_in_customer() -> None:
    variant = _variant()
    user = UserFactory()
    _db_cart(user, [(variant, "1.000")])
    client = Client()
    client.force_login(user)

    _remove(client, variant)

    assert not CartItem.objects.filter(cart__user=user).exists()


def test_htmx_remove_returns_the_cart_body_fragment() -> None:
    variant = _variant()
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    html = _remove(client, variant, HTTP_HX_REQUEST="true").content.decode()

    assert html.startswith('<div id="cart-body"')
    assert "Your cart is empty." in html


def test_remove_of_a_line_that_is_not_in_the_cart_is_a_no_op_not_an_error() -> None:
    in_cart = _variant()
    missing = _variant()
    client = Client()
    _guest_with(client, [(in_cart, "1.000")])

    response = _remove(client, missing, HTTP_HX_REQUEST="true")
    html = response.content.decode()

    assert response.status_code == 200
    assert 'role="alert"' not in html
    assert client.session[GUEST_CART_SESSION_KEY] == {str(in_cart.pk): "1.000"}


def test_update_for_unknown_variant_is_a_clean_error() -> None:
    client = Client()

    response = client.post(
        reverse("cart_update"),
        {"variant_id": "999999", "quantity": "1"},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    assert 'role="alert"' in response.content.decode()


# ---------------------------------------------------------------------------
# An archived line and the CSRF token (Slice 33, D62)
# ---------------------------------------------------------------------------
def test_remove_works_for_a_line_whose_variant_was_archived() -> None:
    variant = _variant()
    variant.is_active = False
    variant.save(update_fields=["is_active", "updated_at"])
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    response = _remove(client, variant)

    assert response.status_code == 302
    assert client.session[GUEST_CART_SESSION_KEY] == {}


def test_update_to_zero_works_for_a_line_whose_variant_was_archived() -> None:
    variant = _variant()
    variant.is_active = False
    variant.save(update_fields=["is_active", "updated_at"])
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    response = _update(client, variant, "0")

    assert response.status_code == 302
    assert client.session[GUEST_CART_SESSION_KEY] == {}


def test_remove_form_on_the_cart_page_carries_a_working_csrf_token() -> None:
    variant = _variant()
    client = Client(enforce_csrf_checks=True)
    _guest_with(client, [(variant, "1.000")])
    html = client.get(reverse("cart_detail")).content.decode()
    match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
    assert match is not None
    token = match.group(1)

    response = client.post(
        reverse("cart_remove"),
        {
            "variant_id": str(variant.pk),
            "csrfmiddlewaretoken": token,
        },
    )

    assert response.status_code == 302
    assert client.session[GUEST_CART_SESSION_KEY] == {}
