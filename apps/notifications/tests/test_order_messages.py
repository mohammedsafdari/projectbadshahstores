"""Tests for the order message texts, with no database (Slice 38a, D69)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.notifications.messages import (
    CUSTOMER_EVENTS,
    EVENT_CANCELLED,
    EVENT_DELIVERED,
    EVENT_OUT_FOR_DELIVERY,
    EVENT_PLACED,
    EVENT_STAFF_NEW_ORDER,
    OrderNotice,
    email_body,
    email_subject,
    sms_text,
)


def make_notice(
    *,
    payment_method: str = "COD",
    order_number: str = "BS/26-27/000123",
    grand_total: Decimal = Decimal("230.00"),
    amount_due_on_delivery: Decimal = Decimal("230.00"),
) -> OrderNotice:
    """One notice with the factory defaults, overridable per test."""
    return OrderNotice(
        order_number=order_number,
        customer_name="Asha Sharma",
        payment_method=payment_method,
        grand_total=grand_total,
        amount_due_on_delivery=amount_due_on_delivery,
        promised_delivery_date=date(2026, 11, 7),
        ship_city="Indore",
        ship_pincode="452012",
        ship_phone="+919876543210",
    )


def test_placed_cod_sms_has_order_number_amount_and_date() -> None:
    expected = (
        "Badshah Stores: Order BS/26-27/000123 is placed. "
        "Pay Rs 230.00 on delivery. Delivery: 07 Nov 2026."
    )

    assert sms_text(EVENT_PLACED, make_notice()) == expected


def test_placed_credit_sms_says_credit_and_has_total() -> None:
    notice = make_notice(
        payment_method="CREDIT",
        amount_due_on_delivery=Decimal("0.00"),
    )
    expected = (
        "Badshah Stores: Order BS/26-27/000123 is placed on your credit account. "
        "Total Rs 230.00. Delivery: 07 Nov 2026."
    )

    assert sms_text(EVENT_PLACED, notice) == expected


def test_cancelled_sms_has_order_number() -> None:
    expected = "Badshah Stores: Order BS/26-27/000123 is cancelled. Call us if you have questions."

    assert sms_text(EVENT_CANCELLED, make_notice()) == expected


def test_out_for_delivery_cod_sms_asks_for_cash_amount() -> None:
    expected = (
        "Badshah Stores: Order BS/26-27/000123 is out for delivery. Please keep Rs 230.00 ready."
    )

    assert sms_text(EVENT_OUT_FOR_DELIVERY, make_notice()) == expected


def test_out_for_delivery_credit_sms_has_no_cash_request() -> None:
    text = sms_text(EVENT_OUT_FOR_DELIVERY, make_notice(payment_method="CREDIT"))

    assert text == "Badshah Stores: Order BS/26-27/000123 is out for delivery."
    assert "Rs" not in text
    assert "ready" not in text


def test_delivered_sms_has_order_number() -> None:
    expected = "Badshah Stores: Order BS/26-27/000123 is delivered. Thank you for shopping with us."

    assert sms_text(EVENT_DELIVERED, make_notice()) == expected


def test_every_customer_sms_is_ascii_and_at_most_160_characters() -> None:
    for payment_method in ("COD", "CREDIT"):
        notice = make_notice(
            payment_method=payment_method,
            order_number="BS/26-27/123456",
        )
        for event in CUSTOMER_EVENTS:
            text = sms_text(event, notice)

            # DLT templates need plain ASCII, and one SMS part is 160 characters.
            assert text.isascii(), text
            assert len(text) <= 160, text
            assert text.startswith("Badshah Stores: ")


def test_amount_is_written_with_two_decimals() -> None:
    notice = make_notice(
        grand_total=Decimal("99.5"),
        amount_due_on_delivery=Decimal("99.5"),
    )

    text = sms_text(EVENT_PLACED, notice)

    assert "Pay Rs 99.50 on delivery." in text
    assert "99.5 " not in text


def test_customer_email_subject_has_order_number_and_event_words() -> None:
    notice = make_notice()
    prefix = "Badshah Stores: order BS/26-27/000123"

    assert email_subject(EVENT_PLACED, notice) == f"{prefix} placed"
    assert email_subject(EVENT_CANCELLED, notice) == f"{prefix} cancelled"
    assert email_subject(EVENT_OUT_FOR_DELIVERY, notice) == f"{prefix} out for delivery"
    assert email_subject(EVENT_DELIVERED, notice) == f"{prefix} delivered"


def test_customer_email_body_has_total_payment_method_and_date() -> None:
    body = email_body(EVENT_PLACED, make_notice(payment_method="CREDIT"))

    assert body.startswith("Hello Asha Sharma,\n")
    assert "Order BS/26-27/000123 is placed on your credit account." in body
    assert "Total: Rs 230.00" in body
    assert "Payment: Credit (khata)" in body
    assert "Delivery: 07 Nov 2026" in body
    assert body.endswith("Thank you,\nBadshah Stores")
    # The email sentence drops the brand prefix that the SMS needs.
    assert "Badshah Stores: Order" not in body


def test_staff_email_has_customer_name_phone_city_and_total() -> None:
    notice = make_notice()

    subject = email_subject(EVENT_STAFF_NEW_ORDER, notice)
    body = email_body(EVENT_STAFF_NEW_ORDER, notice)

    assert subject == "New order BS/26-27/000123: Rs 230.00 (Cash on delivery)"
    assert "Asha Sharma" in body
    assert "+919876543210" in body
    assert "Indore" in body
    assert "452012" in body
    assert "Payment: Cash on delivery" in body
    assert "Total: Rs 230.00" in body
    assert "Amount due on delivery: Rs 230.00" in body
    assert "Delivery: 07 Nov 2026" in body


def test_unknown_event_raises_value_error() -> None:
    notice = make_notice()

    with pytest.raises(ValueError):
        sms_text("NOT_AN_EVENT", notice)
    with pytest.raises(ValueError):
        email_subject("NOT_AN_EVENT", notice)
    with pytest.raises(ValueError):
        email_body("NOT_AN_EVENT", notice)


def test_staff_event_has_no_sms_text() -> None:
    with pytest.raises(ValueError):
        sms_text(EVENT_STAFF_NEW_ORDER, make_notice())
