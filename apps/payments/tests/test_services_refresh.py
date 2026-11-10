"""Tests for the server-to-server payment check (D77)."""

from __future__ import annotations

import logging
from dataclasses import replace
from decimal import Decimal
from unittest.mock import call, patch

import pytest

from apps.notifications.messages import EVENT_PLACED, EVENT_STAFF_NEW_ORDER
from apps.orders.models import Order, OrderStatusLog
from apps.orders.services import OrderNotFoundError
from apps.payments.fake_gateway import FakeGateway
from apps.payments.gateway import PAYMENT_CAPTURED, PAYMENT_FAILED, PaymentGatewayError
from apps.payments.models import Payment, WebhookEvent
from apps.payments.services import handle_gateway_event, refresh_payment_from_gateway
from apps.payments.tests.factories import PaymentFactory
from apps.payments.tests.helpers import (
    NOW,
    clear_settings,
    event_for,
    factory_online_order,
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
    FakeGateway.reset()
    yield
    FakeGateway.reset()
    clear_settings()


def _pending():
    user, order, _row = online_order()
    return user, order, start_payment(order).payment


def test_a_fetched_capture_confirms_the_order_and_notifies_once(
    django_capture_on_commit_callbacks,
) -> None:
    user, order, payment = _pending()
    FakeGateway.seed_payments(
        payment.gateway_order_id,
        [event_for(payment, event_id="evt_fetch")],
    )

    with (
        patch("apps.orders.services.notify_order_event") as notifier,
        django_capture_on_commit_callbacks(execute=True),
    ):
        refresh_payment_from_gateway(user, order.order_number, now=NOW)

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.CAPTURED
    assert payment.captured_at == NOW
    assert order.status == Order.Status.CONFIRMED
    assert notifier.call_args_list == [
        call(order.pk, EVENT_PLACED),
        call(order.pk, EVENT_STAFF_NEW_ORDER),
    ]
    assert WebhookEvent.objects.count() == 0

    # A second page load finds a captured payment and asks the gateway nothing.
    logs = OrderStatusLog.objects.filter(order=order).count()
    refresh_payment_from_gateway(user, order.order_number, now=NOW)
    assert OrderStatusLog.objects.filter(order=order).count() == logs


def test_a_fetched_failure_moves_the_order_to_payment_failed() -> None:
    user, order, payment = _pending()
    FakeGateway.seed_payments(
        payment.gateway_order_id,
        [event_for(payment, kind=PAYMENT_FAILED, event_id="evt_failed")],
    )

    refresh_payment_from_gateway(user, order.order_number, now=NOW)

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.FAILED
    assert order.status == Order.Status.PAYMENT_FAILED


def test_an_authorized_payment_changes_nothing() -> None:
    user, order, payment = _pending()
    FakeGateway.seed_payments(
        payment.gateway_order_id,
        [replace(event_for(payment), kind="payment.authorized")],
    )

    refresh_payment_from_gateway(user, order.order_number, now=NOW)

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.CREATED
    assert order.status == Order.Status.PAYMENT_PENDING
    assert WebhookEvent.objects.count() == 0


def test_a_gateway_error_changes_nothing_and_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user, order, payment = _pending()

    def fail(self, gateway_order_id: str):
        raise PaymentGatewayError("The provider is down.")

    monkeypatch.setattr(FakeGateway, "fetch_order_payments", fail)

    with caplog.at_level(logging.WARNING, logger="apps.payments.services"):
        refresh_payment_from_gateway(user, order.order_number, now=NOW)

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.CREATED
    assert order.status == Order.Status.PAYMENT_PENDING
    assert "Payment check failed" in caplog.text


def test_an_already_captured_payment_makes_no_gateway_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, order, payment = _pending()
    handle_gateway_event(
        event_for(payment, event_id="evt_captured"),
        provider="fake",
        now=NOW,
    )

    def explode(self, gateway_order_id: str):
        raise AssertionError("The provider must not be called.")

    monkeypatch.setattr(FakeGateway, "fetch_order_payments", explode)

    refresh_payment_from_gateway(user, order.order_number, now=NOW)


def test_another_customers_order_is_not_found() -> None:
    _user, order, _payment = _pending()

    with pytest.raises(OrderNotFoundError):
        refresh_payment_from_gateway(UserFactory(), order.order_number, now=NOW)


def test_an_amount_mismatch_is_not_applied_and_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    user, order, payment = _pending()
    FakeGateway.seed_payments(
        payment.gateway_order_id,
        [event_for(payment, amount=Decimal("1.00"))],
    )

    with caplog.at_level(logging.WARNING, logger="apps.payments.services"):
        refresh_payment_from_gateway(user, order.order_number, now=NOW)

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.CREATED
    assert order.status == Order.Status.PAYMENT_PENDING
    assert "amount mismatch" in caplog.text


def test_a_webhook_after_a_fetch_is_a_no_op() -> None:
    user, order, payment = _pending()
    event = event_for(payment, event_id="evt_both")
    FakeGateway.seed_payments(payment.gateway_order_id, [event])
    refresh_payment_from_gateway(user, order.order_number, now=NOW)
    logs = OrderStatusLog.objects.count()

    handle_gateway_event(event, provider="fake", now=NOW)

    payment.refresh_from_db()
    order.refresh_from_db()
    assert order.status == Order.Status.CONFIRMED
    assert OrderStatusLog.objects.count() == logs
    assert WebhookEvent.objects.count() == 1


def test_a_fetch_after_a_webhook_makes_no_gateway_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, order, payment = _pending()
    handle_gateway_event(
        event_for(payment, event_id="evt_webhook"),
        provider="fake",
        now=NOW,
    )

    def explode(self, gateway_order_id: str):
        raise AssertionError("The provider must not be called.")

    monkeypatch.setattr(FakeGateway, "fetch_order_payments", explode)

    refresh_payment_from_gateway(user, order.order_number, now=NOW)


def test_an_order_without_a_payment_does_nothing() -> None:
    order = factory_online_order()

    refresh_payment_from_gateway(order.user, order.order_number, now=NOW)

    assert Payment.objects.filter(order=order).count() == 0


def test_no_gateway_logs_a_warning_and_changes_nothing(
    settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    order = factory_online_order()
    payment = PaymentFactory(order=order)
    settings.PAYMENT_PROVIDER = ""

    with caplog.at_level(logging.WARNING, logger="apps.payments.services"):
        refresh_payment_from_gateway(order.user, order.order_number, now=NOW)

    payment.refresh_from_db()
    assert payment.status == Payment.Status.CREATED
    assert "no gateway" in caplog.text


def test_a_captured_event_wins_over_a_failed_one() -> None:
    user, order, payment = _pending()
    FakeGateway.seed_payments(
        payment.gateway_order_id,
        [
            event_for(payment, kind=PAYMENT_FAILED, event_id="evt_failed"),
            event_for(payment, kind=PAYMENT_CAPTURED, event_id="evt_captured"),
        ],
    )

    refresh_payment_from_gateway(user, order.order_number, now=NOW)

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.CAPTURED
    assert order.status == Order.Status.CONFIRMED
