"""Tests for the order's coupon fields and its money equation (D82)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import IntegrityError

from apps.orders.models import Order
from apps.orders.tests.factories import OrderFactory

pytestmark = pytest.mark.django_db


def _constraint_names() -> set[str]:
    return {constraint.name for constraint in Order._meta.constraints}


def test_an_order_without_a_coupon_keeps_the_full_total() -> None:
    order = OrderFactory()

    assert order.discount_total == Decimal("0.00")
    assert order.delivery_discount == Decimal("0.00")
    assert order.grand_total == Decimal("230.00")


def test_the_equation_with_both_discounts_holds() -> None:
    order = OrderFactory(
        discount_total=Decimal("20.00"),
        delivery_discount=Decimal("30.00"),
        coupon_code="DIWALI10",
        grand_total=Decimal("180.00"),
    )
    order.refresh_from_db()

    # item 200 - item discount 20 + fee 30 - delivery discount 30 = 180
    assert order.grand_total == (
        order.item_total - order.discount_total + order.delivery_fee - order.delivery_discount
    )


def test_a_total_that_ignores_the_item_discount_is_refused() -> None:
    with pytest.raises(IntegrityError):
        OrderFactory(discount_total=Decimal("20.00"), grand_total=Decimal("230.00"))


def test_a_total_that_ignores_the_delivery_discount_is_refused() -> None:
    with pytest.raises(IntegrityError):
        OrderFactory(delivery_discount=Decimal("30.00"), grand_total=Decimal("230.00"))


def test_the_item_discount_cannot_be_negative() -> None:
    with pytest.raises(IntegrityError):
        OrderFactory(discount_total=Decimal("-1.00"), grand_total=Decimal("231.00"))


def test_the_delivery_discount_cannot_be_negative() -> None:
    with pytest.raises(IntegrityError):
        OrderFactory(delivery_discount=Decimal("-1.00"), grand_total=Decimal("261.00"))


def test_the_item_discount_cannot_exceed_the_item_total() -> None:
    with pytest.raises(IntegrityError):
        OrderFactory(discount_total=Decimal("201.00"), grand_total=Decimal("29.00"))


def test_the_item_discount_may_equal_the_item_total() -> None:
    order = OrderFactory(discount_total=Decimal("200.00"), grand_total=Decimal("30.00"))

    assert order.grand_total == Decimal("30.00")


def test_the_delivery_discount_cannot_exceed_the_fee() -> None:
    with pytest.raises(IntegrityError):
        OrderFactory(delivery_discount=Decimal("31.00"), grand_total=Decimal("199.00"))


def test_the_delivery_discount_may_equal_the_fee() -> None:
    order = OrderFactory(delivery_discount=Decimal("30.00"), grand_total=Decimal("200.00"))

    assert order.delivery_discount == order.delivery_fee


def test_the_coupon_code_defaults_to_blank() -> None:
    order = OrderFactory()

    assert order.coupon_code == ""
    field = Order._meta.get_field("coupon_code")
    assert field.default == ""
    assert field.max_length == 20


def test_the_coupon_code_is_stored_as_written() -> None:
    order = OrderFactory(coupon_code="DIWALI-10")

    assert order.coupon_code == "DIWALI-10"


def test_the_grand_total_constraint_keeps_its_name() -> None:
    assert "order_grand_total_matches" in _constraint_names()


def test_the_three_new_constraints_are_named() -> None:
    assert {
        "order_discounts_gte_0",
        "order_item_discount_lte_item_total",
        "order_delivery_discount_lte_fee",
    } <= _constraint_names()
