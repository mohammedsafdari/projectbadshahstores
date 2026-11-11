"""Tests for the order models, their constraints and the append-only log (D64)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import IntegrityError

from apps.catalog.tests.factories import ProductVariantFactory
from apps.core.models import AppendOnlyError
from apps.orders.models import Order, OrderStatusLog
from apps.orders.tests.factories import OrderFactory, OrderItemFactory

pytestmark = pytest.mark.django_db


def test_a_new_order_is_placed_and_paid_on_delivery() -> None:
    order = OrderFactory()

    assert order.status == Order.Status.PLACED
    assert order.payment_method == Order.PaymentMethod.COD
    assert order.amount_due_on_delivery == order.grand_total


def test_order_number_is_unique() -> None:
    OrderFactory(order_number="BS/26-27/000001")

    with pytest.raises(IntegrityError):
        OrderFactory(order_number="BS/26-27/000001")


def test_checkout_group_is_unique() -> None:
    group = uuid4()
    OrderFactory(checkout_group=group)

    with pytest.raises(IntegrityError):
        OrderFactory(checkout_group=group)


def test_order_money_cannot_be_negative() -> None:
    with pytest.raises(IntegrityError):
        OrderFactory(item_total=Decimal("-1.00"), grand_total=Decimal("29.00"))


def test_grand_total_must_match_item_total_plus_delivery_fee() -> None:
    with pytest.raises(IntegrityError):
        OrderFactory(grand_total=Decimal("999.00"))


def test_a_cancelled_order_needs_a_cancelled_at_time() -> None:
    with pytest.raises(IntegrityError):
        OrderFactory(status=Order.Status.CANCELLED, cancelled_at=None)


def test_a_delivered_order_needs_a_delivered_at_time() -> None:
    with pytest.raises(IntegrityError):
        OrderFactory(status=Order.Status.DELIVERED, delivered_at=None)


def test_credit_applied_cannot_be_negative() -> None:
    with pytest.raises(IntegrityError):
        OrderFactory(
            payment_method=Order.PaymentMethod.CREDIT,
            credit_applied=Decimal("-1.00"),
            amount_due_on_delivery=Decimal("231.00"),
        )


def test_credit_applied_cannot_be_above_the_grand_total() -> None:
    with pytest.raises(IntegrityError):
        OrderFactory(
            payment_method=Order.PaymentMethod.CREDIT,
            credit_applied=Decimal("231.00"),
            amount_due_on_delivery=Decimal("-1.00"),
        )


def test_amount_due_must_match_the_credit_part() -> None:
    with pytest.raises(IntegrityError):
        OrderFactory(
            payment_method=Order.PaymentMethod.CREDIT,
            credit_applied=Decimal("100.00"),
            amount_due_on_delivery=Decimal("100.00"),
        )


def test_a_credit_order_needs_a_credit_part_above_zero() -> None:
    with pytest.raises(IntegrityError):
        OrderFactory(
            payment_method=Order.PaymentMethod.CREDIT,
            credit_applied=Decimal("0.00"),
            amount_due_on_delivery=Decimal("230.00"),
        )


def test_a_cod_order_cannot_carry_a_credit_part() -> None:
    with pytest.raises(IntegrityError):
        OrderFactory(
            payment_method=Order.PaymentMethod.COD,
            credit_applied=Decimal("100.00"),
            amount_due_on_delivery=Decimal("130.00"),
        )


def test_the_refund_fee_percent_cannot_be_negative() -> None:
    with pytest.raises(IntegrityError):
        OrderFactory(refund_fee_percent=Decimal("-0.01"))


def test_the_refund_fee_percent_cannot_be_above_ten() -> None:
    with pytest.raises(IntegrityError):
        OrderFactory(refund_fee_percent=Decimal("10.01"))


def test_order_str_is_the_order_number() -> None:
    assert str(OrderFactory(order_number="BS/26-27/000042")) == "BS/26-27/000042"


def test_a_variant_appears_only_once_per_order() -> None:
    order = OrderFactory()
    variant = ProductVariantFactory()
    OrderItemFactory(order=order, variant=variant)

    with pytest.raises(IntegrityError):
        OrderItemFactory(order=order, variant=variant)


def test_quantity_ordered_must_be_above_zero() -> None:
    with pytest.raises(IntegrityError):
        OrderItemFactory(quantity_ordered=Decimal("0.000"))


def test_quantity_fulfilled_cannot_be_negative() -> None:
    with pytest.raises(IntegrityError):
        OrderItemFactory(quantity_fulfilled=Decimal("-1.000"))


def test_order_item_prices_cannot_be_negative() -> None:
    with pytest.raises(IntegrityError):
        OrderItemFactory(mrp=Decimal("-1.00"))


def test_a_status_log_row_cannot_be_saved_twice() -> None:
    log = OrderStatusLog.objects.create(
        order=OrderFactory(),
        from_status="",
        to_status=Order.Status.PLACED,
    )
    log.to_status = Order.Status.CONFIRMED

    with pytest.raises(AppendOnlyError):
        log.save()


def test_a_status_log_row_cannot_be_deleted() -> None:
    log = OrderStatusLog.objects.create(
        order=OrderFactory(),
        from_status="",
        to_status=Order.Status.PLACED,
    )

    with pytest.raises(AppendOnlyError):
        log.delete()


def test_status_log_queryset_update_and_delete_are_blocked() -> None:
    log = OrderStatusLog.objects.create(
        order=OrderFactory(),
        from_status="",
        to_status=Order.Status.PLACED,
    )

    with pytest.raises(AppendOnlyError):
        OrderStatusLog.objects.filter(pk=log.pk).update(note="changed")
    with pytest.raises(AppendOnlyError):
        OrderStatusLog.objects.filter(pk=log.pk).delete()


def test_status_log_bulk_update_is_blocked() -> None:
    log = OrderStatusLog.objects.create(
        order=OrderFactory(),
        from_status="",
        to_status=Order.Status.PLACED,
    )
    log.note = "changed"

    with pytest.raises(AppendOnlyError):
        OrderStatusLog.objects.bulk_update([log], ["note"])
