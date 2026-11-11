"""Test factories for the payment models (D77)."""

from __future__ import annotations

from decimal import Decimal

import factory
from django.utils import timezone

from apps.orders.tests.factories import OrderFactory
from apps.payments.models import Payment, Refund, WebhookEvent


class PaymentFactory(factory.django.DjangoModelFactory):
    """A created online attempt for an order with an online part."""

    class Meta:
        model = Payment

    order = factory.SubFactory(OrderFactory)
    provider = "fake"
    gateway_order_id = factory.Sequence(lambda n: f"fake_order_test_{n + 1}")
    gateway_payment_id = ""
    amount = Decimal("230.00")
    currency = "INR"
    status = Payment.Status.CREATED
    method = ""
    failure_reason = ""
    needs_refund = False
    captured_at = None
    failed_at = None
    raw_response = factory.LazyFunction(dict)


class CapturedPaymentFactory(PaymentFactory):
    """A payment whose money has arrived, with the matching timestamp."""

    status = Payment.Status.CAPTURED
    gateway_payment_id = factory.Sequence(lambda n: f"fake_payment_{n + 1}")
    method = "upi"
    captured_at = factory.LazyFunction(timezone.now)


class FailedPaymentFactory(PaymentFactory):
    """A refused attempt, with the matching timestamp."""

    status = Payment.Status.FAILED
    method = "upi"
    failure_reason = "Payment failed"
    failed_at = factory.LazyFunction(timezone.now)


class RefundFactory(factory.django.DjangoModelFactory):
    """A refund row for a captured payment (D78)."""

    class Meta:
        model = Refund

    payment = factory.SubFactory(CapturedPaymentFactory)
    kind = Refund.Kind.CUSTOMER_CANCEL
    status = Refund.Status.REQUESTED
    amount = Decimal("230.00")
    fee_amount = Decimal("0.00")
    idempotency_key = factory.Sequence(lambda n: f"refund-test-{n + 1}")
    provider_refund_id = ""
    attempt_count = 0
    last_attempt_at = None
    sent_at = None
    processed_at = None
    failure_reason = ""
    raw_response = factory.LazyFunction(dict)


class WebhookEventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WebhookEvent

    provider = "fake"
    event_id = factory.Sequence(lambda n: f"evt_{n + 1}")
    event_type = "payment.captured"
    payload = factory.LazyFunction(dict)
    payment = None
    processed_at = None
    error = ""
