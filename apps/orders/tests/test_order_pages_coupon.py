"""Tests for the discounts on the confirmation and order detail pages (D83)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.orders.models import Order
from apps.orders.tests.checkout_helpers import element_text, has_element
from apps.orders.tests.factories import OrderFactory, OrderItemFactory
from apps.orders.tests.staff_helpers import staff
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _rupees(text: str) -> Decimal:
    """The amount inside a printed rupee label, without its minus sign."""
    return Decimal(text.replace("₹", "").replace("-", "").strip())


def _confirmation_path(order: Order) -> str:
    return reverse("order_confirmation", args=[order.checkout_group])


def _detail_path(order: Order) -> str:
    return reverse("order_detail", args=[order.checkout_group])


def _coded_order(user: User, **overrides: object) -> Order:
    """One order with a 20.00 coupon discount on 200.00 of goods and a 30.00 fee."""
    values: dict[str, object] = {
        "user": user,
        "item_total": Decimal("200.00"),
        "delivery_fee": Decimal("30.00"),
        "discount_total": Decimal("20.00"),
        "delivery_discount": Decimal("0.00"),
        "grand_total": Decimal("210.00"),
        "coupon_code": "DIWALI10",
        "amount_due_on_delivery": Decimal("210.00"),
    }
    values.update(overrides)
    return OrderFactory(**values)


def _plain_order(user: User) -> Order:
    """One order with no coupon at all."""
    return OrderFactory(user=user)


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


# --- the confirmation page ----------------------------------------------------


def test_confirmation_shows_the_coupon_row() -> None:
    user = UserFactory()
    order = _coded_order(user)
    OrderItemFactory(order=order)

    html = _client(user).get(_confirmation_path(order)).content.decode()

    assert "Coupon (DIWALI10)" in html
    assert element_text(html, "data-coupon-discount") == "-₹20.00"
    assert element_text(html, "data-grand-total") == "₹210.00"


def test_confirmation_shows_no_coupon_row_for_a_plain_order() -> None:
    user = UserFactory()
    order = _plain_order(user)
    OrderItemFactory(order=order)

    html = _client(user).get(_confirmation_path(order)).content.decode()

    assert not has_element(html, "data-coupon-discount")
    assert "Coupon (" not in html
    assert element_text(html, "data-grand-total") == f"₹{order.grand_total:.2f}"


# --- the order detail page ----------------------------------------------------


def test_detail_shows_the_coupon_row() -> None:
    user = UserFactory()
    order = _coded_order(user)
    OrderItemFactory(order=order)

    html = _client(user).get(_detail_path(order)).content.decode()

    assert "Coupon (DIWALI10)" in html
    assert element_text(html, "data-coupon-discount") == "-₹20.00"


def test_detail_shows_no_coupon_row_for_a_plain_order() -> None:
    user = UserFactory()
    order = _plain_order(user)
    OrderItemFactory(order=order)

    html = _client(user).get(_detail_path(order)).content.decode()

    assert not has_element(html, "data-coupon-discount")
    assert "Coupon (" not in html


def test_the_cancelled_order_page_still_shows_the_discount() -> None:
    user = UserFactory()
    order = _coded_order(
        user,
        status=Order.Status.CANCELLED,
        cancelled_at=timezone.now(),
    )
    OrderItemFactory(order=order)

    html = _client(user).get(_detail_path(order)).content.decode()

    assert "Coupon (DIWALI10)" in html
    assert element_text(html, "data-coupon-discount") == "-₹20.00"


# --- the delivery discount ----------------------------------------------------


def test_confirmation_shows_the_delivery_discount_row() -> None:
    user = UserFactory()
    order = _coded_order(
        user,
        discount_total=Decimal("0.00"),
        delivery_discount=Decimal("30.00"),
        grand_total=Decimal("200.00"),
        amount_due_on_delivery=Decimal("200.00"),
        coupon_code="FREESHIP",
    )
    OrderItemFactory(order=order)

    html = _client(user).get(_confirmation_path(order)).content.decode()

    assert "Delivery discount" in html
    assert element_text(html, "data-delivery-discount") == "-₹30.00"
    assert element_text(html, "data-grand-total") == "₹200.00"


def test_confirmation_shows_no_delivery_discount_row_for_a_plain_order() -> None:
    user = UserFactory()
    order = _plain_order(user)
    OrderItemFactory(order=order)

    html = _client(user).get(_confirmation_path(order)).content.decode()

    assert not has_element(html, "data-delivery-discount")
    assert "Delivery discount" not in html


def test_the_delivery_line_shows_the_fee_before_the_coupon() -> None:
    user = UserFactory()
    order = _coded_order(
        user,
        discount_total=Decimal("0.00"),
        delivery_discount=Decimal("30.00"),
        grand_total=Decimal("200.00"),
        amount_due_on_delivery=Decimal("200.00"),
        coupon_code="FREESHIP",
    )
    OrderItemFactory(order=order)

    html = _client(user).get(_detail_path(order)).content.decode()

    # The fee line keeps the cart's 30.00; the free delivery is its own row.
    assert element_text(html, "data-delivery-fee") == "₹30.00"
    assert element_text(html, "data-delivery-discount") == "-₹30.00"


# --- the totals add up --------------------------------------------------------


def test_the_confirmation_totals_add_up() -> None:
    user = UserFactory()
    order = _coded_order(user)
    OrderItemFactory(order=order)

    html = _client(user).get(_confirmation_path(order)).content.decode()

    item = _rupees(element_text(html, "data-item-total"))
    coupon = _rupees(element_text(html, "data-coupon-discount"))
    fee = _rupees(element_text(html, "data-delivery-fee"))
    total = _rupees(element_text(html, "data-grand-total"))
    assert item - coupon + fee == total


def test_the_detail_totals_add_up() -> None:
    user = UserFactory()
    order = _coded_order(
        user,
        discount_total=Decimal("0.00"),
        delivery_discount=Decimal("30.00"),
        grand_total=Decimal("200.00"),
        amount_due_on_delivery=Decimal("200.00"),
        coupon_code="FREESHIP",
    )
    OrderItemFactory(order=order)

    html = _client(user).get(_detail_path(order)).content.decode()

    item = _rupees(element_text(html, "data-item-total"))
    fee = _rupees(element_text(html, "data-delivery-fee"))
    delivery_discount = _rupees(element_text(html, "data-delivery-discount"))
    total = _rupees(element_text(html, "data-grand-total"))
    assert item + fee - delivery_discount == total


# --- the list and other people ------------------------------------------------


def test_the_order_list_is_unchanged() -> None:
    user = UserFactory()
    _coded_order(user)
    _plain_order(user)

    html = _client(user).get(reverse("order_list")).content.decode()

    assert "₹210.00" in html
    assert "₹230.00" in html
    assert not has_element(html, "data-coupon-discount")


def test_another_customers_order_detail_is_a_404() -> None:
    owner = UserFactory()
    order = _coded_order(owner)
    stranger = _client(UserFactory())

    response = stranger.get(_detail_path(order))

    assert response.status_code == 404


def test_another_customers_confirmation_is_a_404() -> None:
    owner = UserFactory()
    order = _coded_order(owner)
    stranger = _client(UserFactory())

    response = stranger.get(_confirmation_path(order))

    assert response.status_code == 404


# --- the staff page -----------------------------------------------------------


def test_the_staff_order_detail_shows_the_code_and_both_discounts() -> None:
    user = UserFactory()
    order = _coded_order(
        user,
        discount_total=Decimal("20.00"),
        delivery_discount=Decimal("10.00"),
        grand_total=Decimal("200.00"),
        amount_due_on_delivery=Decimal("200.00"),
    )
    OrderItemFactory(order=order)

    response = _client(staff(User.ROLE_OWNER)).get(
        reverse("staff_order_detail", args=[order.order_number])
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "Coupon discount" in html
    assert "(DIWALI10)" in html
    assert "Delivery discount" in html
    assert "₹20.00" in html
    assert "₹10.00" in html
