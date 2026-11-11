"""Tests for the refund rules the mutation run found unguarded (D78)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from apps.notifications.messages import EVENT_REFUND_PROCESSED
from apps.orders.models import Order
from apps.orders.tests.factories import OrderFactory
from apps.payments.fake_gateway import FakeGateway
from apps.payments.gateway import REFUND_PROCESSED, GatewayEvent, GatewayRefund
from apps.payments.models import Payment, Refund
from apps.payments.refunds import (
    reconcile_refunds,
    refresh_refund_from_gateway,
    send_refund,
)
from apps.payments.services import handle_gateway_event
from apps.payments.tests.factories import CapturedPaymentFactory, RefundFactory
from apps.payments.tests.helpers import NOW, clear_settings, reset_settings

pytestmark = pytest.mark.django_db

CUSTOMER_ONLINE = {
    "payment_method": Order.PaymentMethod.ONLINE,
    "online_amount": Decimal("230.00"),
    "amount_due_on_delivery": Decimal("0.00"),
}


@pytest.fixture(autouse=True)
def _fake_gateway(settings):
    """A clean shop row and the fake provider around every test."""
    reset_settings()
    settings.PAYMENT_PROVIDER = "fake"
    FakeGateway.reset()
    yield
    FakeGateway.reset()
    clear_settings()


def _captured_payment(*, needs_refund: bool = True) -> Payment:
    """A captured online payment on a cancelled order."""
    order = OrderFactory(
        status=Order.Status.CANCELLED,
        cancelled_at=NOW,
        **CUSTOMER_ONLINE,
    )
    return CapturedPaymentFactory(order=order, needs_refund=needs_refund)


def _sent_refund(payment: Payment) -> Refund:
    """One refund that has already reached the provider."""
    return RefundFactory(
        payment=payment,
        status=Refund.Status.SENT,
        amount=payment.amount,
        sent_at=NOW,
        last_attempt_at=NOW,
    )


def _refund_event(payment: Payment, refund: Refund, *, event_id: str) -> GatewayEvent:
    """One verified refund.processed event for this payment and refund."""
    return GatewayEvent(
        event_id=event_id,
        kind=REFUND_PROCESSED,
        gateway_order_id="",
        gateway_payment_id=payment.gateway_payment_id,
        amount=refund.amount,
        method="",
        safe={
            "event": REFUND_PROCESSED,
            "refund_id": "rfnd_survivor",
            "payment_id": payment.gateway_payment_id,
        },
    )


def test_a_processed_refund_event_sends_the_customer_message_once(
    django_capture_on_commit_callbacks,
    monkeypatch,
) -> None:
    sent: list[tuple[int, str]] = []

    def record_notice(order_id: int, event: str) -> None:
        sent.append((order_id, event))

    monkeypatch.setattr("apps.payments.refunds.notify_order_event", record_notice)
    payment = _captured_payment()
    refund = _sent_refund(payment)
    event = _refund_event(payment, refund, event_id="evt_survivor_once")

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        handle_gateway_event(event, provider="fake", now=NOW)

    refund.refresh_from_db()
    assert refund.status == Refund.Status.PROCESSED
    assert len(callbacks) == 1
    assert sent == [(payment.order_id, EVENT_REFUND_PROCESSED)]

    # The provider may deliver the same event again; the customer hears it once.
    with django_capture_on_commit_callbacks(execute=True) as again:
        handle_gateway_event(event, provider="fake", now=NOW)

    assert again == []
    assert len(sent) == 1


def test_a_processed_refresh_sends_the_customer_message_once(
    django_capture_on_commit_callbacks,
    monkeypatch,
) -> None:
    sent: list[tuple[int, str]] = []

    def record_notice(order_id: int, event: str) -> None:
        sent.append((order_id, event))

    monkeypatch.setattr("apps.payments.refunds.notify_order_event", record_notice)
    payment = _captured_payment()
    refund = _sent_refund(payment)
    FakeGateway.seed_refunds(
        payment.gateway_payment_id,
        [
            GatewayRefund(
                gateway_refund_id="fake_refund_seeded",
                gateway_payment_id=payment.gateway_payment_id,
                kind=REFUND_PROCESSED,
                amount=refund.amount,
            )
        ],
    )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        changed = refresh_refund_from_gateway(refund.pk, gateway=FakeGateway(), now=NOW)

    refund.refresh_from_db()
    assert changed is True
    assert refund.status == Refund.Status.PROCESSED
    assert len(callbacks) == 1
    assert sent == [(payment.order_id, EVENT_REFUND_PROCESSED)]

    with django_capture_on_commit_callbacks(execute=True) as again:
        refresh_refund_from_gateway(refund.pk, gateway=FakeGateway(), now=NOW)

    assert again == []
    assert len(sent) == 1


def test_send_fails_a_refund_whose_payment_is_not_captured_and_never_calls_the_gateway(
    monkeypatch,
) -> None:
    calls: list[tuple[str, Decimal, str]] = []
    original = FakeGateway.create_refund

    def record_create(
        gateway_self,
        gateway_payment_id: str,
        amount: Decimal,
        idempotency_key: str,
    ) -> GatewayRefund:
        calls.append((gateway_payment_id, amount, idempotency_key))
        return original(gateway_self, gateway_payment_id, amount, idempotency_key)

    monkeypatch.setattr(FakeGateway, "create_refund", record_create)
    payment = _captured_payment()
    refund = RefundFactory(payment=payment, amount=payment.amount)
    # An ordinary save, not a constraint bypass: a payment that is not captured
    # must also give up its capture time, or the row would break the constraint.
    payment.status = Payment.Status.CREATED
    payment.captured_at = None
    payment.save(update_fields=["status", "captured_at", "updated_at"])

    stored = send_refund(refund.pk, gateway=FakeGateway(), now=NOW)

    assert stored.status == Refund.Status.FAILED
    assert stored.failure_reason == "The payment is not captured."
    assert stored.attempt_count == 0
    assert calls == []


def test_reconcile_skips_money_captured_inside_the_minimum_age(settings) -> None:
    min_age = settings.PAYMENT_RECONCILE_MIN_AGE_MINUTES
    # One minute inside the minimum age, so this capture is too young to touch.
    payment = _captured_payment()
    payment.captured_at = NOW - timedelta(minutes=min_age - 1)
    payment.save(update_fields=["captured_at", "updated_at"])

    reconcile_refunds(now=NOW)

    assert not Refund.objects.filter(payment=payment).exists()


def test_reconcile_handles_money_captured_exactly_at_the_minimum_age(settings) -> None:
    min_age = settings.PAYMENT_RECONCILE_MIN_AGE_MINUTES
    payment = _captured_payment()
    payment.captured_at = NOW - timedelta(minutes=min_age)
    payment.save(update_fields=["captured_at", "updated_at"])

    reconcile_refunds(now=NOW)

    refund = Refund.objects.get(payment=payment)
    assert refund.kind == Refund.Kind.SHOP_CANCEL
    assert refund.amount == payment.amount


def test_reconcile_does_not_check_a_sent_refund_attempted_inside_the_stale_limit(
    settings,
    monkeypatch,
) -> None:
    stale = settings.REFUND_STALE_MINUTES
    calls: list[str] = []

    def record_lookup(gateway_self, gateway_payment_id: str) -> list[GatewayRefund]:
        calls.append(gateway_payment_id)
        return []

    monkeypatch.setattr(FakeGateway, "fetch_payment_refunds", record_lookup)
    payment = _captured_payment()
    refund = _sent_refund(payment)
    # One minute inside the stale limit, so the run must not ask the provider.
    refund.last_attempt_at = NOW - timedelta(minutes=stale - 1)
    refund.save(update_fields=["last_attempt_at", "updated_at"])

    reconcile_refunds(now=NOW)

    assert calls == []


def test_reconcile_checks_a_sent_refund_attempted_exactly_at_the_stale_limit(
    settings,
    monkeypatch,
) -> None:
    stale = settings.REFUND_STALE_MINUTES
    calls: list[str] = []

    def record_lookup(gateway_self, gateway_payment_id: str) -> list[GatewayRefund]:
        calls.append(gateway_payment_id)
        return []

    monkeypatch.setattr(FakeGateway, "fetch_payment_refunds", record_lookup)
    payment = _captured_payment()
    refund = _sent_refund(payment)
    refund.last_attempt_at = NOW - timedelta(minutes=stale)
    refund.save(update_fields=["last_attempt_at", "updated_at"])

    reconcile_refunds(now=NOW)

    assert calls == [payment.gateway_payment_id]
