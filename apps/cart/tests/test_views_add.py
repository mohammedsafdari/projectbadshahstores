"""Tests for the add-to-cart view (D61)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.cart.stores import GUEST_CART_SESSION_KEY
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
    *,
    active: bool = True,
    stock: str = "10.000",
    price: str = "100.00",
    loose: bool = False,
    step: str = "1",
    name: str | None = None,
) -> ProductVariant:
    product_kwargs = {"name_en": name} if name is not None else {}
    product = ProductFactory(is_active=True, **product_kwargs)
    variant = ProductVariantFactory(
        product=product,
        is_active=active,
        is_sold_loose=loose,
        unit=ProductVariant.Unit.KG if loose else ProductVariant.Unit.PACKET,
        pack_size=Decimal("1.000"),
        quantity_step=Decimal(step),
        selling_price=Decimal(price),
        mrp=Decimal(price),
    )
    if Decimal(stock):
        InventoryMovementFactory(variant=variant, quantity_change=Decimal(stock))
    return variant


def _post(client: Client, variant: ProductVariant, quantity: str, **extra):
    data = {"variant_id": str(variant.pk), "quantity": quantity}
    # Keyword arguments starting with HTTP_ are request headers, not form
    # fields, so htmx detection works the way the browser sends it.
    headers = {key: extra.pop(key) for key in list(extra) if key.startswith("HTTP_")}
    data.update(extra)
    return client.post(reverse("cart_add"), data, **headers)


def _htmx_post(client: Client, variant: ProductVariant, quantity: str, **extra):
    return _post(client, variant, quantity, **extra, HTTP_HX_REQUEST="true")


def test_add_requires_post() -> None:
    assert Client().get(reverse("cart_add")).status_code == 405


def test_add_without_csrf_token_is_refused() -> None:
    variant = _variant()
    client = Client(enforce_csrf_checks=True)

    assert _post(client, variant, "1").status_code == 403


def test_guest_add_stores_the_line_in_the_session() -> None:
    variant = _variant()
    client = Client()

    _post(client, variant, "2")

    assert client.session[GUEST_CART_SESSION_KEY] == {str(variant.pk): "2.000"}


def test_logged_in_add_stores_the_line_in_the_database() -> None:
    variant = _variant()
    user = UserFactory()
    client = Client()
    client.force_login(user)

    _post(client, variant, "2")

    assert user.cart.items.get().variant_id == variant.pk


def test_htmx_add_returns_the_success_fragment_with_status_200() -> None:
    variant = _variant()
    client = Client()

    response = _htmx_post(client, variant, "2")
    html = response.content.decode()

    assert response.status_code == 200
    assert "Added 2 packs." in html
    assert "View cart" in html
    assert "<html" not in html


def test_htmx_add_updates_the_header_count_out_of_band() -> None:
    variant = _variant()
    client = Client()

    html = _htmx_post(client, variant, "2").content.decode()

    assert 'id="cart-count"' in html
    assert 'hx-swap-oob="true"' in html
    assert ">1<" in html


def test_htmx_add_error_is_a_200_fragment_with_an_alert() -> None:
    variant = _variant()
    client = Client()

    response = _htmx_post(client, variant, "not-a-number")

    assert response.status_code == 200
    assert 'role="alert"' in response.content.decode()


def test_htmx_add_over_available_shows_the_available_amount() -> None:
    variant = _variant(stock="2.000")
    client = Client()

    html = _htmx_post(client, variant, "5").content.decode()

    assert 'role="alert"' in html
    assert "2 packs" in html


def test_htmx_add_of_an_archived_variant_shows_a_generic_message() -> None:
    variant = _variant(active=False, stock="0.000")
    client = Client()

    html = _htmx_post(client, variant, "1").content.decode()

    assert 'role="alert"' in html
    assert "This item is not available right now." in html
    assert "archived" not in html.lower()


def test_non_htmx_add_redirects_with_a_message() -> None:
    variant = _variant()
    client = Client()

    response = _post(client, variant, "2")
    page = client.get(response["Location"]).content.decode()

    assert response.status_code == 302
    assert response["Location"] == reverse("cart_detail")
    assert "Added 2 packs." in page


def test_non_htmx_add_error_redirects_with_an_error_message() -> None:
    variant = _variant()
    client = Client()

    response = _post(client, variant, "nope")
    page = client.get(response["Location"]).content.decode()

    assert response.status_code == 302
    assert "Enter a quantity like 1 or 1.250." in page


def test_next_url_to_another_host_is_ignored() -> None:
    variant = _variant()
    client = Client()

    response = _post(client, variant, "1", next="https://evil.example/")

    assert response["Location"] == reverse("cart_detail")


def test_unknown_variant_id_is_a_clean_error_not_a_500() -> None:
    client = Client()

    response = client.post(
        reverse("cart_add"),
        {"variant_id": "999999", "quantity": "1"},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    assert 'role="alert"' in response.content.decode()


def test_garbage_quantity_is_a_clean_error_not_a_500() -> None:
    variant = _variant()
    client = Client()

    response = _htmx_post(client, variant, "abc")

    assert response.status_code == 200
    assert 'role="alert"' in response.content.decode()


def test_adding_twice_sums_the_quantities() -> None:
    variant = _variant()
    client = Client()
    _post(client, variant, "1")

    _post(client, variant, "1")

    assert client.session[GUEST_CART_SESSION_KEY] == {str(variant.pk): "2.000"}


def test_add_does_not_accept_a_price_field() -> None:
    variant = _variant(price="100.00")
    client = Client()

    response = _post(client, variant, "1", price="0.01")
    page = client.get(response["Location"]).content.decode()

    assert "₹100.00" in page
    assert "₹0.01" not in page


def test_responses_are_not_cacheable() -> None:
    variant = _variant()
    client = Client()

    response = _htmx_post(client, variant, "1")

    assert "no-store" in response["Cache-Control"]


def test_a_quantity_with_four_decimals_is_refused_with_a_message() -> None:
    variant = _variant()
    client = Client()

    response = _htmx_post(client, variant, "1.2345")

    assert response.status_code == 200
    assert 'role="alert"' in response.content.decode()
    assert variant.pk not in client.session.get(GUEST_CART_SESSION_KEY, {})
