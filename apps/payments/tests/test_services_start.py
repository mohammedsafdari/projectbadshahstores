"""Tests for starting (or retrying) an online payment (D77)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.orders.models import Order, OrderStatusLog
from apps.orders.services import OrderNotFoundError
from apps.payments.fake_gateway import fake_gateway_order_id
from apps.payments.gateway import PAYMENT_FAILED, GatewayNotConfigured
from apps.payments.models import Payment
from apps.payments.services import (
    PaymentAlreadyCapturedError,
    PaymentNotAllowedError,
    handle_gateway_event,
    start_online_payment,
)
from apps.payments.tests.factories import CapturedPaymentFactory, PaymentFactory
from apps.payments.tests.helpers import (
    NOW,
    clear_settings,
    event_for,
    factory_online_order,
    online_order,
    reset_settings,
    start_payment,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _online_shop(settings):
    """A clean settings row and the fake provider for every test."""
    reset_settings()
    settings.PAYMENT_PROVIDER = "fake"
    yield
    clear_settings()


def test_start_creates_an_attempt_for_a_placed_order() -> None:
    order = factory_online_order(status=Order.Status.PLACED)

    started = start_payment(order)

    assert started.payment.status == Payment.Status.CREATED
    assert started.payment.provider == "fake"
    assert started.payment.amount == Decimal("230.00")
    assert started.payment.gateway_order_id == fake_gateway_order_id(order.order_number)
    assert started.session.gateway_order_id == started.payment.gateway_order_id


def test_start_moves_a_placed_order_to_payment_pending() -> None:
    order = factory_online_order(status=Order.Status.PLACED)

    start_payment(order)

    order.refresh_from_db()
    assert order.status == Order.Status.PAYMENT_PENDING
    log = OrderStatusLog.objects.filter(order=order).last()
    assert log is not None
    assert log.from_status == Order.Status.PLACED
    assert log.to_status == Order.Status.PAYMENT_PENDING


def test_start_twice_reuses_the_one_created_attempt() -> None:
    order = factory_online_order()

    first = start_payment(order)
    second = start_payment(order)

    assert second.payment.pk == first.payment.pk
    assert Payment.objects.filter(order=order).count() == 1


def test_start_after_a_failure_resets_the_same_attempt() -> None:
    order = factory_online_order()
    first = start_payment(order)
    payment = first.payment
    handle_gateway_event(
        event_for(payment, kind=PAYMENT_FAILED),
        provider="fake",
        now=NOW,
    )
    payment.refresh_from_db()
    assert payment.status == Payment.Status.FAILED
    before = payment.updated_at

    second = start_payment(order)

    payment.refresh_from_db()
    assert second.payment.pk == payment.pk
    assert payment.status == Payment.Status.CREATED
    assert payment.failure_reason == ""
    assert payment.failed_at is None
    assert payment.gateway_payment_id == ""
    assert payment.updated_at > before


def test_start_reports_an_already_captured_payment() -> None:
    order = factory_online_order()
    CapturedPaymentFactory(order=order)

    with pytest.raises(PaymentAlreadyCapturedError):
        start_payment(order)


def test_start_refuses_an_order_with_no_online_part() -> None:
    order = factory_online_order()
    order.online_amount = Decimal("0.00")
    order.payment_method = Order.PaymentMethod.COD
    order.amount_due_on_delivery = order.grand_total
    order.save(
        update_fields=[
            "online_amount",
            "payment_method",
            "amount_due_on_delivery",
            "updated_at",
        ]
    )

    with pytest.raises(PaymentNotAllowedError):
        start_payment(order)


def test_start_reports_an_unknown_order_as_not_found() -> None:
    order = factory_online_order()

    with pytest.raises(OrderNotFoundError):
        start_online_payment(order.user, order.order_number + "-other", now=NOW)


def test_start_refuses_a_cancelled_order() -> None:
    order = factory_online_order()
    order.status = Order.Status.CANCELLED
    order.cancelled_at = NOW
    order.cancel_reason = "Test"
    order.save(update_fields=["status", "cancelled_at", "cancel_reason", "updated_at"])

    with pytest.raises(PaymentNotAllowedError):
        start_payment(order)


def test_start_refuses_a_delivered_order() -> None:
    order = factory_online_order()
    order.status = Order.Status.DELIVERED
    order.delivered_at = NOW
    order.save(update_fields=["status", "delivered_at", "updated_at"])

    with pytest.raises(PaymentNotAllowedError):
        start_payment(order)


def test_start_needs_a_configured_gateway(settings) -> None:
    settings.PAYMENT_PROVIDER = ""
    order = factory_online_order()

    with pytest.raises(GatewayNotConfigured):
        start_payment(order)

    assert Payment.objects.filter(order=order).count() == 0


def test_start_works_for_a_real_online_order() -> None:
    _user, order, _row = online_order()

    started = start_payment(order)

    assert started.payment.amount == order.online_amount == Decimal("230.00")
    assert order.status == Order.Status.PAYMENT_PENDING


def test_start_sends_no_order_notice() -> None:
    order = factory_online_order(status=Order.Status.PLACED)

    with patch("apps.payments.services.notify_order_event") as notifier:
        start_payment(order)
        notifier.assert_not_called()


def test_a_second_start_adds_no_second_log() -> None:
    order = factory_online_order(status=Order.Status.PLACED)
    start_payment(order)

    start_payment(order)

    order.refresh_from_db()
    assert order.status == Order.Status.PAYMENT_PENDING
    assert OrderStatusLog.objects.filter(order=order).count() == 1


def test_start_returns_the_amount_the_gateway_should_charge() -> None:
    payment = PaymentFactory(order=factory_online_order(), amount=Decimal("17.50"))
    order = payment.order
    order.online_amount = Decimal("17.50")
    order.amount_due_on_delivery = order.grand_total - order.credit_applied - Decimal("17.50")
    order.save(update_fields=["online_amount", "amount_due_on_delivery", "updated_at"])

    started = start_payment(order)

    assert started.session.amount == payment.amount == Decimal("17.50")
