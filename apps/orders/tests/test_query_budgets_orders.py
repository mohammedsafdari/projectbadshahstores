"""Query budgets for the checkout and the customer order pages (Slice 49, D90).

Each budget is the count measured while building this slice plus two, and the
second test of each page shows the count does not grow with the data.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import Address, User
from apps.accounts.services import create_address
from apps.cart.models import Cart
from apps.cart.tests.factories import CartFactory, CartItemFactory
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.core.models import ShopSettings
from apps.delivery.tests.factories import DeliveryZoneFactory, PinCodeZoneFactory
from apps.inventory.tests.factories import InventoryMovementFactory
from apps.orders import views
from apps.orders.tests.factories import OrderFactory, OrderItemFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PINCODE = "452012"

# measured + 2, per page.
CHECKOUT_PAGE_QUERY_BUDGET = 16
ORDER_LIST_PAGE_QUERY_BUDGET = 7
ORDER_DETAIL_PAGE_QUERY_BUDGET = 13


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """One settings row and an empty cache, so no test sees a stale cache."""
    cache.clear()
    ShopSettings.load()
    yield
    cache.clear()


def _serve(pincode: str = PINCODE) -> None:
    zone = DeliveryZoneFactory()
    PinCodeZoneFactory(zone=zone, pincode=pincode)


def _customer_with_address() -> User:
    """One customer with a served address, a cart and a login session."""
    _serve()
    user = UserFactory()
    create_address(
        user,
        recipient_name="Asha Sharma",
        recipient_phone="+919876543210",
        line1="12 Test Road",
        line2="",
        landmark="",
        city="Indore",
        state="Madhya Pradesh",
        pincode=PINCODE,
        label="HOME",
    )
    return user


def _variant(name: str):
    """One packet variant with stock, under a visible brand and category."""
    brand = BrandFactory(is_active=True)
    category = CategoryFactory(is_active=True)
    product = ProductFactory(name_en=name, brand=brand, category=category, is_active=True)
    variant = ProductVariantFactory(
        product=product,
        is_active=True,
        unit=ProductVariant.Unit.PACKET,
        pack_size=Decimal("1.000"),
        selling_price=Decimal("100.00"),
        mrp=Decimal("120.00"),
    )
    InventoryMovementFactory(variant=variant, quantity_change=Decimal("50.000"))
    return variant


def _cart(user: User, variants) -> None:
    cart = CartFactory(user=user)
    for variant in variants:
        CartItemFactory(cart=cart, variant=variant, quantity=Decimal("1.000"))


def _count(client: Client, url: str) -> int:
    """The number of queries one GET of this page runs, which must answer 200.

    The cache is emptied first, so the shop settings are read again on every
    measured request and two measurements always start from the same place.
    """
    cache.clear()
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    assert response.status_code == 200
    return len(queries)


def _assert_budget(count: int, budget: int) -> None:
    assert count <= budget, f"{count} queries, budget {budget}"


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------
def test_checkout_page_query_budget(client: Client) -> None:
    user = _customer_with_address()
    _cart(user, [_variant(f"Budget Item {number}") for number in range(3)])
    client.force_login(user)
    # The first GET stores the checkout group in the session, which is one
    # write; the page is opened once so the measurement sees a warm session.
    client.get(reverse("checkout"))

    count = _count(client, reverse("checkout"))

    _assert_budget(count, CHECKOUT_PAGE_QUERY_BUDGET)


def test_checkout_page_addresses_do_not_add_queries(client: Client) -> None:
    """More saved addresses must not mean one more query each (D90)."""
    user = _customer_with_address()
    _cart(user, [_variant("Budget Item"), _variant("Second Item")])
    client.force_login(user)
    client.get(reverse("checkout"))

    small = _count(client, reverse("checkout"))

    for number in range(20):
        Address.objects.create(
            user=user,
            recipient_name=f"Person {number}",
            recipient_phone="+919876543210",
            line1=f"{number} Test Road",
            city="Indore",
            state="Madhya Pradesh",
            pincode=PINCODE,
            label="HOME",
        )
    large = _count(client, reverse("checkout"))

    assert large == small


def test_checkout_page_reads_the_addresses_once(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One read of the address cards per GET, not one for the choice and one to draw (D90)."""
    user = _customer_with_address()
    _cart(user, [_variant(f"Budget Item {number}") for number in range(3)])
    client.force_login(user)
    client.get(reverse("checkout"))
    calls: list[str] = []
    real_read = views._address_rows
    # The stub records the call and then does the real read, so the page still
    # renders; ``or`` returns the list the read returns.
    monkeypatch.setattr(
        "apps.orders.views._address_rows",
        lambda by_user, selected: calls.append(selected) or real_read(by_user, selected),
    )

    response = client.get(reverse("checkout"))

    assert response.status_code == 200
    assert len(calls) == 1


def test_checkout_page_queries_do_not_grow(client: Client) -> None:
    user = _customer_with_address()
    _cart(user, [_variant(f"Budget Item {number}") for number in range(3)])
    client.force_login(user)
    client.get(reverse("checkout"))

    small = _count(client, reverse("checkout"))

    cart = Cart.objects.get(user=user)
    for number in range(17):
        CartItemFactory(
            cart=cart, variant=_variant(f"Extra Item {number}"), quantity=Decimal("1.000")
        )
    large = _count(client, reverse("checkout"))

    assert large == small


# ---------------------------------------------------------------------------
# Order list
# ---------------------------------------------------------------------------
def test_order_list_page_query_budget(client: Client) -> None:
    user = UserFactory()
    for _ in range(3):
        OrderFactory(user=user)
    client.force_login(user)

    count = _count(client, reverse("order_list"))

    _assert_budget(count, ORDER_LIST_PAGE_QUERY_BUDGET)


def test_order_list_page_queries_do_not_grow(client: Client) -> None:
    user = UserFactory()
    for _ in range(3):
        OrderFactory(user=user)
    client.force_login(user)

    small = _count(client, reverse("order_list"))

    for _ in range(17):
        OrderFactory(user=user)
    large = _count(client, reverse("order_list"))

    assert large == small


# ---------------------------------------------------------------------------
# Order detail
# ---------------------------------------------------------------------------
def _order_with_items(user: User, items: int):
    order = OrderFactory(user=user)
    for _ in range(items):
        OrderItemFactory(order=order)
    return order


def test_order_detail_page_query_budget(client: Client) -> None:
    user = UserFactory()
    order = _order_with_items(user, items=3)
    client.force_login(user)

    count = _count(client, reverse("order_detail", args=[order.checkout_group]))

    _assert_budget(count, ORDER_DETAIL_PAGE_QUERY_BUDGET)


def test_order_detail_page_queries_do_not_grow(client: Client) -> None:
    user = UserFactory()
    order = _order_with_items(user, items=3)
    client.force_login(user)
    url = reverse("order_detail", args=[order.checkout_group])

    small = _count(client, url)

    wide = _order_with_items(user, items=20)
    large = _count(client, reverse("order_detail", args=[wide.checkout_group]))

    assert large == small
