"""Tests for applying verified gateway events (D77)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import call, patch

import pytest

from apps.inventory.models import StockReservation
from apps.notifications.messages import EVENT_PLACED, EVENT_STAFF_NEW_ORDER
from apps.orders.models import Order, OrderStatusLog
from apps.orders.services import transition_order
from apps.orders.tests.factories import OrderFactory
from apps.payments.gateway import PAYMENT_CAPTURED, PAYMENT_FAILED, GatewayEvent
from apps.payments.models import Payment, WebhookEvent
from apps.payments.services import handle_gateway_event
from apps.payments.tests.factories import PaymentFactory
from apps.payments.tests.helpers import (
    NOW,
    clear_settings,
    event_for,
    online_order,
    reset_settings,
    start_payment,
)
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _online_shop(settings):
    """A clean settings row and the fake provider for every test."""
    reset_settings()
    settings.PAYMENT_PROVIDER = "fake"
    yield
    clear_settings()


def _pending() -> tuple[Order, Payment]:
    """A real online order waiting for its first captured payment."""
    _user, order, _row = online_order()
    return order, start_payment(order).payment


def test_capture_marks_the_payment_with_the_gateway_values() -> None:
    _order, payment = _pending()
    before = payment.updated_at

    handle_gateway_event(event_for(payment), provider="fake", now=NOW)

    payment.refresh_from_db()
    assert payment.status == Payment.Status.CAPTURED
    assert payment.gateway_payment_id == "pay_1"
    assert payment.method == "upi"
    assert payment.captured_at == NOW
    assert payment.failure_reason == ""
    assert payment.raw_response["event"] == PAYMENT_CAPTURED
    assert payment.updated_at > before


def test_capture_moves_the_order_through_paid_to_confirmed() -> None:
    order, payment = _pending()

    handle_gateway_event(event_for(payment), provider="fake", now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.CONFIRMED
    logs = list(OrderStatusLog.objects.filter(order=order).order_by("pk"))
    assert [log.to_status for log in logs][-2:] == [Order.Status.PAID, Order.Status.CONFIRMED]


def test_capture_turns_the_checkout_hold_into_a_fulfilment_hold() -> None:
    order, payment = _pending()

    handle_gateway_event(event_for(payment), provider="fake", now=NOW)

    hold = StockReservation.objects.get(checkout_group=order.checkout_group)
    assert hold.kind == StockReservation.Kind.HELD_FOR_FULFILMENT
    assert hold.expires_at is None


def test_a_capture_after_an_expired_hold_reserves_a_fresh_group() -> None:
    order, payment = _pending()
    old_group = order.checkout_group
    before = order.updated_at
    # An expired checkout hold cannot be promoted; a new hold must be created.
    StockReservation.objects.filter(checkout_group=old_group).update(
        expires_at=NOW - timedelta(minutes=1)
    )

    handle_gateway_event(event_for(payment, event_id="evt_expired"), provider="fake", now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.CONFIRMED
    assert order.checkout_group != old_group
    assert order.updated_at > before
    hold = StockReservation.objects.get(checkout_group=order.checkout_group)
    assert hold.kind == StockReservation.Kind.HELD_FOR_FULFILMENT


def test_capture_sends_the_placed_and_staff_notices_once(
    django_capture_on_commit_callbacks,
) -> None:
    order, payment = _pending()

    with (
        patch("apps.orders.services.notify_order_event") as notifier,
        django_capture_on_commit_callbacks(execute=True),
    ):
        handle_gateway_event(event_for(payment), provider="fake", now=NOW)

    assert notifier.call_args_list == [
        call(order.pk, EVENT_PLACED),
        call(order.pk, EVENT_STAFF_NEW_ORDER),
    ]


def test_a_repeated_event_id_is_applied_only_once() -> None:
    _order, payment = _pending()
    event = event_for(payment, event_id="evt_same")
    handle_gateway_event(event, provider="fake", now=NOW)
    captured_logs = OrderStatusLog.objects.count()

    handle_gateway_event(event, provider="fake", now=NOW)

    assert WebhookEvent.objects.count() == 1
    assert OrderStatusLog.objects.count() == captured_logs


def test_a_different_event_id_cannot_capture_twice() -> None:
    order, payment = _pending()
    handle_gateway_event(event_for(payment, event_id="evt_1"), provider="fake", now=NOW)
    logs = OrderStatusLog.objects.count()

    handle_gateway_event(event_for(payment, event_id="evt_2"), provider="fake", now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.CONFIRMED
    assert OrderStatusLog.objects.count() == logs


def test_failure_marks_the_payment_and_the_order_but_keeps_the_hold() -> None:
    order, payment = _pending()
    before = payment.updated_at

    handle_gateway_event(
        event_for(payment, kind=PAYMENT_FAILED),
        provider="fake",
        now=NOW,
    )

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.FAILED
    assert payment.failed_at == NOW
    assert payment.failure_reason == "Payment failed"
    assert payment.updated_at > before
    assert order.status == Order.Status.PAYMENT_FAILED
    hold = StockReservation.objects.get(checkout_group=order.checkout_group)
    assert hold.status == StockReservation.Status.ACTIVE
    assert hold.kind == StockReservation.Kind.CHECKOUT_PENDING_PAYMENT


def test_the_provider_error_text_is_kept_as_the_failure_reason() -> None:
    _order, payment = _pending()
    event = replace(
        event_for(payment, kind=PAYMENT_FAILED),
        raw={"error": "card declined"},
    )

    handle_gateway_event(event, provider="fake", now=NOW)

    payment.refresh_from_db()
    assert payment.failure_reason == "card declined"


def test_a_failed_event_does_not_overwrite_a_captured_payment() -> None:
    order, payment = _pending()
    handle_gateway_event(event_for(payment), provider="fake", now=NOW)

    handle_gateway_event(
        event_for(payment, kind=PAYMENT_FAILED, event_id="evt_late_failure"),
        provider="fake",
        now=NOW,
    )

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.CAPTURED
    assert order.status == Order.Status.CONFIRMED


def test_an_amount_mismatch_is_reported_and_changes_nothing() -> None:
    order, payment = _pending()

    webhook = handle_gateway_event(
        event_for(payment, amount=Decimal("1.00")),
        provider="fake",
        now=NOW,
    )

    payment.refresh_from_db()
    order.refresh_from_db()
    assert webhook.error == "Amount does not match the payment"
    assert payment.status == Payment.Status.CREATED
    assert order.status == Order.Status.PAYMENT_PENDING


def test_an_unknown_gateway_order_is_reported() -> None:
    _order, payment = _pending()
    event = replace(event_for(payment), gateway_order_id="fake_order_unknown")

    webhook = handle_gateway_event(event, provider="fake", now=NOW)

    assert webhook.error == "Payment not found"
    assert webhook.payment is None
    assert webhook.processed_at == NOW


def test_an_event_without_an_id_gets_a_uuid() -> None:
    _order, payment = _pending()

    webhook = handle_gateway_event(
        event_for(payment, event_id=""),
        provider="fake",
        now=NOW,
    )

    # A UUID string is 36 characters, and the database also enforces uniqueness.
    assert len(webhook.event_id) == 36
    assert WebhookEvent.objects.filter(event_id=webhook.event_id).count() == 1


def test_the_webhook_row_keeps_the_provider_type_payload_and_payment() -> None:
    _order, payment = _pending()

    webhook = handle_gateway_event(event_for(payment), provider="fake", now=NOW)

    assert webhook.provider == "fake"
    assert webhook.event_type == PAYMENT_CAPTURED
    assert webhook.payload["event"] == PAYMENT_CAPTURED
    assert webhook.payment_id == payment.pk
    assert webhook.processed_at == NOW


def test_processing_a_stored_webhook_moves_its_updated_at() -> None:
    _order, payment = _pending()
    webhook = WebhookEvent.objects.create(
        provider="fake",
        event_id="evt_stored",
        event_type=PAYMENT_CAPTURED,
    )
    before = webhook.updated_at

    handled = handle_gateway_event(
        event_for(payment, event_id="evt_stored"),
        provider="fake",
        now=NOW,
    )

    assert handled.pk == webhook.pk
    assert handled.updated_at > before
    assert handled.payment_id == payment.pk


def test_a_late_capture_on_a_cancelled_order_asks_for_a_refund() -> None:
    order, payment = _pending()
    transition_order(
        order.pk,
        Order.Status.CANCELLED,
        actor=order.user,
        reason="Customer called",
        now=NOW,
    )

    handle_gateway_event(event_for(payment), provider="fake", now=NOW)

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.CAPTURED
    assert payment.needs_refund is True
    assert order.status == Order.Status.CANCELLED


def test_a_late_capture_on_a_delivered_order_asks_for_a_refund() -> None:
    order = OrderFactory(
        user=UserFactory(),
        status=Order.Status.DELIVERED,
        payment_method=Order.PaymentMethod.ONLINE,
        online_amount=Decimal("230.00"),
        amount_due_on_delivery=Decimal("0.00"),
        delivered_at=NOW,
    )
    payment = PaymentFactory(order=order)

    handle_gateway_event(event_for(payment, event_id="evt_late"), provider="fake", now=NOW)

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.needs_refund is True
    assert order.status == Order.Status.DELIVERED


def test_a_capture_that_cannot_restock_cancels_the_order_and_asks_for_a_refund(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order, payment = _pending()
    # Simulate a stock shortage: the real helper returns False when the hold
    # cannot be recreated.
    monkeypatch.setattr(
        "apps.payments.services._restock_for_capture",
        lambda order, *, now: False,
    )

    handle_gateway_event(event_for(payment), provider="fake", now=NOW)

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.needs_refund is True
    assert order.status == Order.Status.CANCELLED


def test_a_capture_on_an_already_confirmed_order_never_sends_new_notices(
    django_capture_on_commit_callbacks,
) -> None:
    order = online_order()[1]
    order.status = Order.Status.CONFIRMED
    order.save(update_fields=["status", "updated_at"])
    payment = PaymentFactory(order=order)

    with (
        patch("apps.orders.services.notify_order_event") as notifier,
        django_capture_on_commit_callbacks(execute=True),
    ):
        handle_gateway_event(
            event_for(payment, event_id="evt_confirmed"),
            provider="fake",
            now=NOW,
        )

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.needs_refund is True
    assert order.status == Order.Status.CONFIRMED
    notifier.assert_not_called()


def test_an_unknown_event_kind_changes_nothing() -> None:
    order, payment = _pending()
    event = GatewayEvent(
        event_id="evt_unknown",
        kind="payment.refunded",
        gateway_order_id=payment.gateway_order_id,
        gateway_payment_id="pay_1",
        amount=payment.amount,
        method="upi",
    )

    webhook = handle_gateway_event(event, provider="fake", now=NOW)

    payment.refresh_from_db()
    order.refresh_from_db()
    assert webhook.processed_at == NOW
    assert payment.status == Payment.Status.CREATED
    assert order.status == Order.Status.PAYMENT_PENDING
