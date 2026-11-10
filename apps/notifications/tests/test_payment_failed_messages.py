"""Tests for the failed-online-payment message (D77)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.notifications.messages import (
    EVENT_PAYMENT_FAILED,
    EVENT_PLACED,
    EVENT_STAFF_NEW_ORDER,
    OrderNotice,
    email_body,
    email_subject,
    sms_text,
)
from apps.notifications.order_notices import notify_order_event
from apps.notifications.tests.recording import RecordingSmsBackend
from apps.orders.tests.checkout_helpers import clear_settings, reset_settings
from apps.orders.tests.factories import OrderFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

RECORDING_BACKEND = "apps.notifications.tests.recording.RecordingSmsBackend"
CUSTOMER_EMAIL = "asha@example.com"
CART_LINK = "https://shop.example/cart/"


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
    payment_method: str = "ONLINE",
    online_amount: Decimal = Decimal("230.00"),
    credit_applied: Decimal = Decimal("0.00"),
    cart_url: str = CART_LINK,
) -> OrderNotice:
    """One notice with an online part and the cart link from a message."""
    return OrderNotice(
        order_number="BS/26-27/000123",
        customer_name="Asha Sharma",
        payment_method=payment_method,
        grand_total=Decimal("230.00"),
        amount_due_on_delivery=Decimal("0.00"),
        promised_delivery_date=date(2026, 11, 7),
        ship_city="Indore",
        ship_pincode="452012",
        ship_phone="+919876543210",
        online_amount=online_amount,
        credit_applied=credit_applied,
        cart_url=cart_url,
    )


def test_the_failed_payment_sms_names_the_order_and_the_cart_link() -> None:
    expected = (
        "Badshah Stores: Order BS/26-27/000123 was not placed because the payment did "
        "not go through. Your cart is saved at https://shop.example/cart/."
    )

    assert sms_text(EVENT_PAYMENT_FAILED, make_notice()) == expected


def test_a_missing_cart_link_still_reads_as_a_sentence() -> None:
    text = sms_text(EVENT_PAYMENT_FAILED, make_notice(cart_url=""))

    assert "Your cart is saved at your cart." in text


def test_the_failed_payment_email_subject_has_the_order_number() -> None:
    subject = email_subject(EVENT_PAYMENT_FAILED, make_notice())

    assert subject == "Badshah Stores: order BS/26-27/000123 payment failed"


def test_the_failed_payment_email_body_carries_the_cart_link() -> None:
    body = email_body(EVENT_PAYMENT_FAILED, make_notice())

    assert CART_LINK in body
    assert "did not go through" in body


def test_the_failed_payment_email_does_not_claim_the_money_arrived() -> None:
    body = email_body(EVENT_PAYMENT_FAILED, make_notice())

    assert "Payment:" not in body
    assert "Paid online" not in body


def test_the_placed_email_for_a_pure_online_order_says_paid_online() -> None:
    body = email_body(EVENT_PLACED, make_notice())
    subject = email_subject(EVENT_PLACED, make_notice())

    assert "Rs 230.00 is paid online." in body
    assert "Payment: Paid online" in body
    assert subject == "Badshah Stores: order BS/26-27/000123 placed"


def test_the_placed_sms_for_a_split_khata_and_online_order_names_both_parts() -> None:
    notice = make_notice(
        payment_method="CREDIT",
        online_amount=Decimal("130.00"),
        credit_applied=Decimal("100.00"),
    )

    text = sms_text(EVENT_PLACED, notice)

    assert "Rs 130.00 is paid online" in text
    assert "Rs 100.00 is on your credit account" in text


def test_the_staff_email_shows_the_online_part_and_the_method() -> None:
    body = email_body(EVENT_STAFF_NEW_ORDER, make_notice())
    subject = email_subject(EVENT_STAFF_NEW_ORDER, make_notice())

    assert "Payment: Paid online" in body
    assert "Paid online: Rs 230.00" in body
    assert "Amount due on delivery: Rs 0.00" in body
    assert "(Online)" in subject


def test_a_failed_payment_sends_both_channels(settings, mailoutbox) -> None:
    settings.ORDER_SMS_ENABLED = True
    settings.ORDER_EMAIL_ENABLED = True
    settings.SMS_BACKEND = RECORDING_BACKEND
    settings.SITE_BASE_URL = "https://shop.example"
    order = OrderFactory(user=UserFactory(email=CUSTOMER_EMAIL))

    notify_order_event(order.pk, EVENT_PAYMENT_FAILED)

    assert len(RecordingSmsBackend.messages) == 1
    phone, text = RecordingSmsBackend.messages[0]
    assert phone == order.user.phone
    assert f"https://shop.example{reverse('cart_detail')}" in text
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [CUSTOMER_EMAIL]
    assert "payment failed" in mailoutbox[0].subject


def test_a_blank_site_base_url_keeps_the_cart_link_relative(settings, mailoutbox) -> None:
    settings.ORDER_SMS_ENABLED = True
    settings.SMS_BACKEND = RECORDING_BACKEND
    settings.ORDER_EMAIL_ENABLED = False
    settings.SITE_BASE_URL = ""
    order = OrderFactory(user=UserFactory())

    notify_order_event(order.pk, EVENT_PAYMENT_FAILED)

    _phone, text = RecordingSmsBackend.messages[0]
    assert f"Your cart is saved at {reverse('cart_detail')}." in text
    assert "http" not in text
