"""Tests for cancelling an order as staff (D79).

A staff cancellation reuses the existing cancel path: the holds are
released, the khata charge is reversed, a captured online payment is
refunded and the customer gets the cancel message (D69, D78).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.credit.models import CreditAccount, CreditEntry
from apps.inventory.models import InventoryMovement, StockReservation
from apps.inventory.services import get_on_hand
from apps.notifications.messages import EVENT_CANCELLED
from apps.orders.models import Order, OrderItem, OrderStatusLog
from apps.orders.services import CancelReasonRequiredError, InvalidTransitionError
from apps.orders.staff_services import pack_and_send_out, staff_cancel_order
from apps.orders.tests.checkout_helpers import (
    address,
    clear_settings,
    credit_user,
    fill_cart,
    place_credit,
    reset_settings,
    serve,
    variant,
)
from apps.orders.tests.factories import OrderFactory
from apps.orders.tests.staff_helpers import NOW, order_with_hold, staff
from apps.payments.models import Refund
from apps.payments.tests.factories import CapturedPaymentFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before every test."""
    reset_settings()
    yield
    clear_settings()


def _return_movements():
    return InventoryMovement.objects.filter(
        movement_type=InventoryMovement.MovementType.RETURN,
    )


def test_staff_cancel_requires_a_reason() -> None:
    order = OrderFactory(status=Order.Status.CONFIRMED)

    with pytest.raises(CancelReasonRequiredError):
        staff_cancel_order(order.pk, actor=staff(), reason="", now=NOW)


def test_staff_cancel_blank_reason_is_refused() -> None:
    order = OrderFactory(status=Order.Status.CONFIRMED)

    with pytest.raises(CancelReasonRequiredError):
        staff_cancel_order(order.pk, actor=staff(), reason="   ", now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.CONFIRMED
    assert order.cancel_reason == ""
    assert not OrderStatusLog.objects.filter(to_status=Order.Status.CANCELLED).exists()


def test_staff_cancel_allowed_from_confirmed_packing_and_out() -> None:
    orders = [
        OrderFactory(status=Order.Status.CONFIRMED),
        OrderFactory(status=Order.Status.PACKING),
        OrderFactory(status=Order.Status.OUT_FOR_DELIVERY),
    ]

    for order in orders:
        staff_cancel_order(order.pk, actor=staff(), reason="Out of stock", now=NOW)

    statuses = [Order.objects.get(pk=order.pk).status for order in orders]
    assert statuses == [
        Order.Status.CANCELLED,
        Order.Status.CANCELLED,
        Order.Status.CANCELLED,
    ]


def test_staff_cancel_delivered_is_refused() -> None:
    order = OrderFactory(status=Order.Status.DELIVERED, delivered_at=NOW)

    with pytest.raises(InvalidTransitionError):
        staff_cancel_order(order.pk, actor=staff(), reason="Too late", now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.DELIVERED


def test_staff_cancel_releases_reservations_before_send_out() -> None:
    order, _row, hold = order_with_hold(status=Order.Status.PACKING)

    staff_cancel_order(order.pk, actor=staff(), reason="Out of stock", now=NOW)

    hold.refresh_from_db()
    assert hold.status == StockReservation.Status.RELEASED
    assert hold.released_at == NOW


def test_staff_cancel_after_send_out_returns_stock() -> None:
    order, row, _hold = order_with_hold()
    item = order.items.get()
    pack_and_send_out(order.pk, actor=staff(), now=NOW)
    assert get_on_hand(row) == Decimal("8")
    item.refresh_from_db()
    before = item.updated_at

    staff_cancel_order(order.pk, actor=staff(), reason="Customer refused", now=NOW)

    assert get_on_hand(row) == Decimal("10")
    returns = _return_movements()
    assert returns.count() == 1
    assert returns.get().quantity_change == Decimal("2")
    # The line is cancelled with the goods that came back, and its timestamp
    # moves with it.
    item.refresh_from_db()
    assert item.item_status == OrderItem.ItemStatus.CANCELLED
    assert item.updated_at > before


def test_staff_cancel_reverses_credit_charge() -> None:
    user = credit_user(limit="1000.00")
    serve()
    address_row = address(user)
    fill_cart(user, variant())
    order = place_credit(user, address_row).order

    staff_cancel_order(order.pk, actor=staff(), reason="Out of stock", now=NOW)

    reversal = CreditEntry.objects.get(
        order=order,
        entry_type=CreditEntry.EntryType.CANCEL_REVERSAL,
    )
    assert reversal.amount == Decimal("-230.00")
    assert CreditAccount.objects.get(user=user).balance == Decimal("0.00")


def test_staff_cancel_online_paid_order_creates_one_refund() -> None:
    order = OrderFactory(
        status=Order.Status.CONFIRMED,
        payment_method=Order.PaymentMethod.ONLINE,
        online_amount=Decimal("230.00"),
        amount_due_on_delivery=Decimal("0.00"),
    )
    payment = CapturedPaymentFactory(order=order)

    staff_cancel_order(order.pk, actor=staff(), reason="Customer called the shop", now=NOW)

    refunds = Refund.objects.filter(payment=payment)
    assert refunds.count() == 1
    assert refunds.get().kind == Refund.Kind.SHOP_CANCEL
    assert refunds.get().amount == Decimal("230.00")
    assert refunds.get().fee_amount == Decimal("0.00")


def test_staff_cancel_sends_the_cancel_notice(
    django_capture_on_commit_callbacks,
) -> None:
    order = OrderFactory(status=Order.Status.CONFIRMED)

    with (
        patch("apps.orders.services.notify_order_event") as notify,
        django_capture_on_commit_callbacks(execute=True),
    ):
        staff_cancel_order(order.pk, actor=staff(), reason="Out of stock", now=NOW)

    notify.assert_called_once_with(order.pk, EVENT_CANCELLED)


def test_staff_cancel_logs_reason_and_actor() -> None:
    actor = staff()
    order = OrderFactory(status=Order.Status.CONFIRMED)

    staff_cancel_order(order.pk, actor=actor, reason="Item was not available", now=NOW)

    order.refresh_from_db()
    assert order.cancel_reason == "Item was not available"
    log = OrderStatusLog.objects.get(order=order, to_status=Order.Status.CANCELLED)
    assert log.actor_id == actor.pk
    assert "Item was not available" in log.note
