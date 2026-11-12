"""Tests for the invoice hooks in the send-out and the staff cancel (D80)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from apps.orders.invoices import ShopDetailsMissingError, mark_invoice_cancelled
from apps.orders.models import Invoice, Order
from apps.orders.services import (
    CUSTOMER_CANCELLABLE,
    CustomerNotVerifiedError,
    InvalidTransitionError,
    cancel_order_by_customer,
)
from apps.orders.staff_services import (
    StockHoldUnavailableError,
    pack_and_send_out,
    staff_cancel_order,
)
from apps.orders.tests.factories import OrderFactory, OrderItemFactory
from apps.orders.tests.invoice_helpers import (
    fill_shop_details,
    gst_variant,
    packing_order,
    sent_out_order,
)
from apps.orders.tests.staff_helpers import NOW, hold_for, staff
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_send_out_creates_the_invoice() -> None:
    order, invoice = sent_out_order()

    assert invoice.order_id == order.pk
    order.refresh_from_db()
    assert order.status == Order.Status.OUT_FOR_DELIVERY
    # One product line and the one delivery-fee line.
    assert invoice.lines.count() == 2


def test_the_invoice_uses_the_fulfilled_quantity() -> None:
    row = gst_variant(price="50.00", gst="5.00")
    order, invoice = sent_out_order(lines=[(row, "2")])

    item = order.items.get()
    assert item.quantity_fulfilled == Decimal("2.000")
    assert invoice.lines.get(is_fee_line=False).quantity == Decimal("2.000")


def test_a_refused_gate_creates_no_invoice() -> None:
    customer = UserFactory(phone_verified_at=None, email_verified_at=None)
    order, _variants = packing_order(customer=customer)

    with pytest.raises(CustomerNotVerifiedError):
        pack_and_send_out(order.pk, actor=staff(), now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.PACKING
    assert not Invoice.objects.exists()


def test_a_short_hold_creates_no_invoice() -> None:
    customer = UserFactory()
    first = gst_variant()
    second = gst_variant()
    group = uuid4()
    # Only the first line has a hold, so the second line refuses the send-out.
    hold_for(first, customer, group=group, quantity="2")
    order = OrderFactory(
        user=customer,
        status=Order.Status.PACKING,
        checkout_group=group,
    )
    OrderItemFactory(order=order, variant=first, quantity_ordered=Decimal("2"))
    OrderItemFactory(order=order, variant=second, quantity_ordered=Decimal("2"))

    with pytest.raises(StockHoldUnavailableError):
        pack_and_send_out(order.pk, actor=staff(), now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.PACKING
    assert not Invoice.objects.exists()


def test_a_blank_shop_gstin_creates_no_invoice() -> None:
    fill_shop_details(gstin="")
    order, _variants = packing_order()

    with pytest.raises(ShopDetailsMissingError):
        pack_and_send_out(order.pk, actor=staff(), now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.PACKING
    assert not Invoice.objects.exists()


def test_staff_cancel_marks_the_invoice_cancelled() -> None:
    order, invoice = sent_out_order()

    staff_cancel_order(order.pk, actor=staff(), reason="The customer refused it", now=NOW)

    invoice.refresh_from_db()
    order.refresh_from_db()
    assert invoice.cancelled_at == NOW
    assert order.status == Order.Status.CANCELLED


def test_staff_cancel_keeps_the_invoice_number() -> None:
    order, invoice = sent_out_order()
    before = invoice.invoice_number

    staff_cancel_order(order.pk, actor=staff(), reason="Refused", now=NOW)

    invoice.refresh_from_db()
    assert invoice.invoice_number == before
    assert Invoice.objects.filter(invoice_number=before).count() == 1
    # The cancelled invoice keeps its place, so the next number is still 2.
    _next_order, next_invoice = sent_out_order()
    assert next_invoice.invoice_number == "INV/26-27/000002"


def test_staff_cancel_stores_the_reason() -> None:
    order, invoice = sent_out_order()

    staff_cancel_order(order.pk, actor=staff(), reason="  Item was damaged  ", now=NOW)

    invoice.refresh_from_db()
    assert invoice.cancel_reason == "Item was damaged"


def test_staff_cancel_of_an_unsent_order_has_no_invoice() -> None:
    order = OrderFactory(status=Order.Status.CONFIRMED)

    staff_cancel_order(order.pk, actor=staff(), reason="Out of stock", now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.CANCELLED
    assert not Invoice.objects.exists()


def test_a_customer_cancel_never_reaches_a_sent_out_order() -> None:
    order, invoice = sent_out_order()

    # The customer may cancel only before packing starts, so a sent-out order
    # never reaches the customer cancel path and needs no hook there (D80).
    with pytest.raises(InvalidTransitionError):
        cancel_order_by_customer(order.user, order.pk, reason="Changed my mind")

    assert CUSTOMER_CANCELLABLE == (Order.Status.PLACED, Order.Status.CONFIRMED)
    invoice.refresh_from_db()
    assert invoice.cancelled_at is None


def test_mark_invoice_cancelled_before_send_out_returns_none() -> None:
    order = OrderFactory(status=Order.Status.CONFIRMED)

    result = mark_invoice_cancelled(order, "Customer changed their mind", now=NOW)

    assert result is None
    assert not Invoice.objects.exists()


def test_marking_the_same_invoice_cancelled_twice_changes_nothing() -> None:
    order, invoice = sent_out_order()
    staff_cancel_order(order.pk, actor=staff(), reason="Refused", now=NOW)

    again = mark_invoice_cancelled(order, "A different reason", now=NOW)

    invoice.refresh_from_db()
    assert again is not None
    assert invoice.cancel_reason == "Refused"
    assert invoice.cancelled_at == NOW
