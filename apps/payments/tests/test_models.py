"""Model tests for Payment and WebhookEvent (D77)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from apps.payments.models import Payment
from apps.payments.tests.factories import (
    CapturedPaymentFactory,
    FailedPaymentFactory,
    PaymentFactory,
    WebhookEventFactory,
)

pytestmark = pytest.mark.django_db


def test_a_new_payment_starts_created_and_needs_no_refund() -> None:
    payment = PaymentFactory()

    assert payment.status == Payment.Status.CREATED
    assert payment.needs_refund is False
    assert payment.captured_at is None
    assert payment.failed_at is None
    assert payment.currency == "INR"
    assert payment.provider == "fake"


def test_payment_string_names_the_provider_and_its_order() -> None:
    payment = PaymentFactory(gateway_order_id="fake_order_abc")

    assert str(payment) == "fake fake_order_abc (CREATED)"


def test_payment_amount_must_be_above_zero() -> None:
    with pytest.raises(IntegrityError):
        PaymentFactory(amount=Decimal("0.00"))


def test_a_captured_payment_needs_a_capture_time() -> None:
    with pytest.raises(IntegrityError):
        CapturedPaymentFactory(captured_at=None)


def test_a_created_payment_cannot_have_a_capture_time() -> None:
    with pytest.raises(IntegrityError):
        PaymentFactory(captured_at=timezone.now())


def test_a_failed_payment_needs_a_failure_time() -> None:
    with pytest.raises(IntegrityError):
        FailedPaymentFactory(failed_at=None)


def test_a_created_payment_cannot_have_a_failure_time() -> None:
    with pytest.raises(IntegrityError):
        PaymentFactory(failed_at=timezone.now())


def test_full_clean_reports_a_missing_capture_time_under_all() -> None:
    # full_clean() checks the model's own constraints before any database write.
    payment = Payment(
        order=PaymentFactory().order,
        provider="fake",
        gateway_order_id="fake_order_clean",
        amount=Decimal("1.00"),
        status=Payment.Status.CAPTURED,
    )

    with pytest.raises(ValidationError) as raised:
        payment.full_clean()

    assert "__all__" in raised.value.message_dict


def test_payments_are_ordered_newest_first() -> None:
    first = PaymentFactory()
    second = PaymentFactory()

    assert list(Payment.objects.all())[:2] == [second, first]


def test_a_payment_belongs_to_an_order_through_payments() -> None:
    payment = PaymentFactory()

    assert list(payment.order.payments.all()) == [payment]


def test_webhook_event_string_names_the_provider_and_event() -> None:
    webhook = WebhookEventFactory(event_id="evt_abc")

    assert str(webhook) == "fake evt_abc"


def test_a_webhook_event_id_can_be_stored_only_once() -> None:
    WebhookEventFactory(event_id="evt_same")

    with pytest.raises(IntegrityError):
        WebhookEventFactory(event_id="evt_same")


def test_a_webhook_event_starts_unprocessed_with_no_payment() -> None:
    webhook = WebhookEventFactory()

    assert webhook.processed_at is None
    assert webhook.payment is None
    assert webhook.error == ""
    assert webhook.payload == {}


def test_a_webhook_event_can_point_at_a_payment() -> None:
    payment = PaymentFactory()

    webhook = WebhookEventFactory(payment=payment)

    assert list(payment.webhook_events.all()) == [webhook]
