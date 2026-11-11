"""Tests for the refund messages (D78)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.notifications.messages import (
    EVENT_REFUND_PROCESSED,
    EVENT_STAFF_REFUND_FAILED,
    OrderNotice,
    email_body,
    email_subject,
    sms_text,
)
from apps.notifications.order_notices import notify_order_event
from apps.notifications.tests.recording import RecordingSmsBackend
from apps.orders.models import Order
from apps.orders.tests.checkout_helpers import clear_settings, reset_settings, set_settings
from apps.orders.tests.factories import OrderFactory
from apps.payments.models import Refund
from apps.payments.tests.factories import CapturedPaymentFactory, RefundFactory
from apps.payments.tests.helpers import NOW
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

RECORDING_BACKEND = "apps.notifications.tests.recording.RecordingSmsBackend"
CUSTOMER_EMAIL = "asha@example.com"
SHOP_EMAIL = "shop@example.com"


@pytest.fixture(autouse=True)
def notice_environment():
    """A clean settings row and recorded SMS list around every test."""
    reset_settings()
    RecordingSmsBackend.messages.clear()
    yield
    RecordingSmsBackend.messages.clear()
    clear_settings()


def make_notice(
    *,
    refund_amount: Decimal = Decimal("224.25"),
    refund_fee: Decimal = Decimal("5.75"),
) -> OrderNotice:
    """One notice for a cancelled online order with a refund."""
    return OrderNotice(
        order_number="BS/26-27/000123",
        customer_name="Asha Sharma",
        payment_method="ONLINE",
        grand_total=Decimal("230.00"),
        amount_due_on_delivery=Decimal("0.00"),
        promised_delivery_date=date(2026, 11, 7),
        ship_city="Indore",
        ship_pincode="452012",
        ship_phone="+919876543210",
        online_amount=Decimal("230.00"),
        refund_amount=refund_amount,
        refund_fee=refund_fee,
    )


def test_the_refund_sms_names_the_amount_and_the_fee() -> None:
    text = sms_text(EVENT_REFUND_PROCESSED, make_notice())

    assert text == (
        "Badshah Stores: Order BS/26-27/000123 is cancelled. "
        "Rs 224.25 has been refunded and a Rs 5.75 cancellation fee was kept."
    )


def test_a_refund_without_a_fee_does_not_mention_one() -> None:
    text = sms_text(
        EVENT_REFUND_PROCESSED,
        make_notice(refund_amount=Decimal("230.00"), refund_fee=Decimal("0.00")),
    )

    assert "Rs 230.00 has been refunded to you." in text
    assert "fee" not in text


def test_the_refund_email_subject_has_the_order_number() -> None:
    subject = email_subject(EVENT_REFUND_PROCESSED, make_notice())

    assert subject == "Badshah Stores: order BS/26-27/000123 refunded"


def test_the_refund_email_shows_the_refund_and_the_fee_and_no_payment_line() -> None:
    body = email_body(EVENT_REFUND_PROCESSED, make_notice())

    assert "Refunded: Rs 224.25" in body
    assert "Cancellation fee kept: Rs 5.75" in body
    # A refunded order must not still read as if the money was with the shop.
    assert "Payment: Paid online" not in body
    # A cancelled order has no delivery left to promise.
    assert "Delivery:" not in body


def test_the_staff_refund_failed_subject_and_body_name_the_refund() -> None:
    subject = email_subject(EVENT_STAFF_REFUND_FAILED, make_notice())
    body = email_body(EVENT_STAFF_REFUND_FAILED, make_notice())

    assert subject == "Badshah Stores: refund failed for order BS/26-27/000123 (Rs 224.25)"
    assert "Refund: Rs 224.25" in body
    assert "provider" in body


def _refunded_order():
    """A cancelled online order with one processed refund."""
    order = OrderFactory(
        user=UserFactory(email=CUSTOMER_EMAIL),
        status=Order.Status.CANCELLED,
        cancelled_at=NOW,
        payment_method=Order.PaymentMethod.ONLINE,
        online_amount=Decimal("230.00"),
        amount_due_on_delivery=Decimal("0.00"),
    )
    payment = CapturedPaymentFactory(order=order, needs_refund=False)
    RefundFactory(
        payment=payment,
        amount=Decimal("224.25"),
        fee_amount=Decimal("5.75"),
        status=Refund.Status.PROCESSED,
        processed_at=NOW,
    )
    return order, payment


def test_a_processed_refund_sends_both_customer_channels(settings, mailoutbox) -> None:
    settings.ORDER_SMS_ENABLED = True
    settings.ORDER_EMAIL_ENABLED = True
    settings.SMS_BACKEND = RECORDING_BACKEND
    order, _payment = _refunded_order()

    notify_order_event(order.pk, EVENT_REFUND_PROCESSED)

    assert len(RecordingSmsBackend.messages) == 1
    _phone, text = RecordingSmsBackend.messages[0]
    assert "Rs 224.25 has been refunded" in text
    assert "Rs 5.75 cancellation fee" in text
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [CUSTOMER_EMAIL]


def test_a_failed_refund_emails_the_shop_and_not_the_customer(settings, mailoutbox) -> None:
    settings.ORDER_EMAIL_ENABLED = True
    set_settings(email=SHOP_EMAIL)
    order, _payment = _refunded_order()

    notify_order_event(order.pk, EVENT_STAFF_REFUND_FAILED)

    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [SHOP_EMAIL]
    assert "refund failed" in mailoutbox[0].subject.lower()
