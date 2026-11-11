"""Tests for refund webhook events (D78)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.orders.models import Order
from apps.orders.tests.factories import OrderFactory
from apps.payments.gateway import (
    REFUND_FAILED,
    REFUND_PROCESSED,
    REFUND_UNKNOWN,
    GatewayEvent,
)
from apps.payments.models import Refund, WebhookEvent
from apps.payments.services import handle_gateway_event
from apps.payments.tests.factories import CapturedPaymentFactory, RefundFactory
from apps.payments.tests.helpers import NOW, clear_settings, reset_settings, set_settings
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

CUSTOMER_ONLINE = {
    "payment_method": Order.PaymentMethod.ONLINE,
    "online_amount": Decimal("230.00"),
    "amount_due_on_delivery": Decimal("0.00"),
}


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """A clean settings row around every test."""
    reset_settings()
    yield
    clear_settings()


def _refund(*, status: str = Refund.Status.SENT, fee: str = "0.00") -> tuple:
    """A captured payment with one open refund and its order."""
    order = OrderFactory(
        user=UserFactory(),
        status=Order.Status.CANCELLED,
        cancelled_at=NOW,
        **CUSTOMER_ONLINE,
    )
    payment = CapturedPaymentFactory(order=order, needs_refund=True)
    amount = payment.amount - Decimal(fee)
    refund = RefundFactory(
        payment=payment,
        amount=amount,
        fee_amount=Decimal(fee),
        status=status,
        sent_at=NOW,
        last_attempt_at=NOW,
    )
    return payment, refund


def _event(
    payment,
    refund,
    *,
    kind: str,
    amount: Decimal | None = None,
    event_id: str = "evt_refund_1",
    refund_id: str = "rfnd_1",
) -> GatewayEvent:
    """One neutral refund event for this payment and refund."""
    return GatewayEvent(
        event_id=event_id,
        kind=kind,
        gateway_order_id="",
        gateway_payment_id=payment.gateway_payment_id,
        amount=refund.amount if amount is None else amount,
        method="",
        safe={
            "event": kind,
            "refund_id": refund_id,
            "payment_id": payment.gateway_payment_id,
        },
    )


def test_a_processed_refund_event_finishes_the_refund() -> None:
    payment, refund = _refund()

    webhook = handle_gateway_event(
        _event(payment, refund, kind=REFUND_PROCESSED),
        provider="fake",
        now=NOW,
    )

    payment.refresh_from_db()
    refund.refresh_from_db()
    assert webhook.processed_at == NOW
    assert webhook.error == ""
    assert webhook.payment_id == payment.pk
    assert refund.status == Refund.Status.PROCESSED
    assert refund.processed_at == NOW
    assert refund.provider_refund_id == "rfnd_1"
    # The refund duty ends only now.
    assert payment.needs_refund is False


def test_a_failed_refund_event_marks_the_refund_failed_and_emails_the_shop(
    django_capture_on_commit_callbacks,
    mailoutbox,
    settings,
) -> None:
    settings.ORDER_EMAIL_ENABLED = True
    set_settings(email="shop@example.com")
    payment, refund = _refund()

    with django_capture_on_commit_callbacks(execute=True):
        webhook = handle_gateway_event(
            _event(payment, refund, kind=REFUND_FAILED),
            provider="fake",
            now=NOW,
        )

    refund.refresh_from_db()
    assert webhook.error == ""
    assert refund.status == Refund.Status.FAILED
    assert refund.failure_reason == "The refund failed."
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == ["shop@example.com"]
    assert "refund failed" in mailoutbox[0].subject.lower()


def test_a_refund_amount_mismatch_changes_nothing() -> None:
    payment, refund = _refund()

    webhook = handle_gateway_event(
        _event(payment, refund, kind=REFUND_PROCESSED, amount=Decimal("1.00")),
        provider="fake",
        now=NOW,
    )

    payment.refresh_from_db()
    refund.refresh_from_db()
    assert webhook.error == "Amount does not match the refund"
    assert refund.status == Refund.Status.SENT
    assert refund.processed_at is None
    assert payment.needs_refund is True


def test_an_unknown_refund_event_is_stored_and_ignored() -> None:
    payment, refund = _refund()
    event = _event(payment, refund, kind=REFUND_UNKNOWN)
    event = GatewayEvent(
        event_id=event.event_id,
        kind=REFUND_UNKNOWN,
        gateway_order_id="",
        gateway_payment_id=payment.gateway_payment_id,
        amount=event.amount,
        method="",
        safe={"event": REFUND_UNKNOWN, "provider_event": "refund.created"},
    )

    webhook = handle_gateway_event(event, provider="fake", now=NOW)

    refund.refresh_from_db()
    stored = WebhookEvent.objects.get(event_id=event.event_id)
    assert webhook.pk == stored.pk
    assert stored.error == "Unknown refund event"
    assert stored.processed_at == NOW
    assert refund.status == Refund.Status.SENT


def test_a_refund_event_without_a_payment_is_recorded() -> None:
    _payment, refund = _refund()
    event = GatewayEvent(
        event_id="evt_no_payment",
        kind=REFUND_PROCESSED,
        gateway_order_id="",
        gateway_payment_id="pay_unknown",
        amount=refund.amount,
        method="",
        safe={"refund_id": "rfnd_1"},
    )

    webhook = handle_gateway_event(event, provider="fake", now=NOW)

    assert webhook.payment_id is None
    assert webhook.error == "Payment not found"


def test_a_refund_event_without_a_refund_is_recorded() -> None:
    order = OrderFactory(
        user=UserFactory(),
        status=Order.Status.CANCELLED,
        cancelled_at=NOW,
        **CUSTOMER_ONLINE,
    )
    payment = CapturedPaymentFactory(order=order, needs_refund=True)
    event = GatewayEvent(
        event_id="evt_no_refund",
        kind=REFUND_PROCESSED,
        gateway_order_id="",
        gateway_payment_id=payment.gateway_payment_id,
        amount=payment.amount,
        method="",
        safe={"refund_id": "rfnd_1"},
    )

    webhook = handle_gateway_event(event, provider="fake", now=NOW)

    assert webhook.error == "Refund not found"
    assert not Refund.objects.filter(payment=payment).exists()


def test_the_same_refund_event_is_applied_once() -> None:
    payment, refund = _refund()
    event = _event(payment, refund, kind=REFUND_PROCESSED, event_id="evt_once")

    first = handle_gateway_event(event, provider="fake", now=NOW)
    second = handle_gateway_event(event, provider="fake", now=NOW)

    assert first.pk == second.pk
    assert WebhookEvent.objects.filter(event_id="evt_once").count() == 1
    refund.refresh_from_db()
    assert refund.status == Refund.Status.PROCESSED


def test_a_late_failed_event_does_not_undo_a_processed_refund() -> None:
    payment, refund = _refund()
    handle_gateway_event(
        _event(payment, refund, kind=REFUND_PROCESSED, event_id="evt_done"),
        provider="fake",
        now=NOW,
    )

    handle_gateway_event(
        _event(payment, refund, kind=REFUND_FAILED, event_id="evt_late"),
        provider="fake",
        now=NOW,
    )

    refund.refresh_from_db()
    assert refund.status == Refund.Status.PROCESSED


def test_a_processed_refund_keeps_its_fee_and_the_payment_adds_up() -> None:
    payment, refund = _refund(fee="5.75")

    assert refund.fee_amount == Decimal("5.75")
    assert refund.amount + refund.fee_amount == payment.amount
