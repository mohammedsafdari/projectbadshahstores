"""Tests for the online-payment wording on the order pages (D77)."""

from __future__ import annotations

from decimal import Decimal

from apps.orders.models import Order
from apps.orders.presentation import (
    PAID_CANNOT_CANCEL_MESSAGE,
    PAID_ONLINE_LABEL,
    PAY_ONLINE_LABEL,
    PAYMENT_FAILED_MESSAGE,
    REMAINDER_HEADING,
    online_amount_label,
    online_sentence,
    online_unavailable_message,
    page_text,
    paid_online_sentence,
    payment_method_label,
    status_sentence,
    timeline_label,
)


def test_the_summary_says_pay_online_before_the_money_arrives() -> None:
    assert online_amount_label(paid=False) == "Pay online"


def test_the_summary_says_paid_online_only_after_capture() -> None:
    assert online_amount_label(paid=True) == "Paid online"


def test_the_online_sentence_shows_the_amount_to_pay() -> None:
    assert online_sentence(Decimal("230.00")) == "Pay ₹230.00 online now."


def test_the_paid_sentence_shows_the_amount_already_paid() -> None:
    assert paid_online_sentence(Decimal("130.00")) == "Paid online: ₹130.00."


def test_the_remainder_heading_is_the_question_the_customer_answers() -> None:
    assert str(REMAINDER_HEADING) == "How would you like to pay the rest?"


def test_the_two_online_labels_are_different_words() -> None:
    assert str(PAY_ONLINE_LABEL) == "Pay online"
    assert str(PAID_ONLINE_LABEL) == "Paid online"


def test_the_unavailable_message_tells_the_customer_what_to_choose() -> None:
    message = online_unavailable_message()

    assert "not available" in message
    assert "cash on delivery" in message


def test_the_payment_failed_message_offers_both_ways_forward() -> None:
    message = str(PAYMENT_FAILED_MESSAGE)

    assert "try again" in message
    assert "update your cart" in message


def test_the_paid_cancel_message_asks_the_customer_to_call() -> None:
    message = str(PAID_CANNOT_CANCEL_MESSAGE)

    assert "online payment" in message
    assert "call the shop" in message


def test_an_online_order_has_a_payment_method_label() -> None:
    assert payment_method_label(Order.PaymentMethod.ONLINE) == "Online payment"


def test_the_online_statuses_have_plain_sentences() -> None:
    assert status_sentence(Order.Status.PAYMENT_PENDING) == "We are waiting for your payment."
    assert status_sentence(Order.Status.PAYMENT_FAILED) == "The payment did not go through."


def test_the_online_statuses_have_short_timeline_labels() -> None:
    assert timeline_label(Order.Status.PAYMENT_PENDING) == "Waiting for payment"
    assert timeline_label(Order.Status.PAID) == "Payment received"


def test_the_shared_page_text_carries_every_new_label() -> None:
    text = page_text()

    assert text["pay_online_label"] == "Pay online"
    assert text["paid_online_label"] == "Paid online"
    assert text["try_again_button"] == "Try again"
    assert text["update_cart_link"] == "Update cart"
    assert text["payment_failed_message"] == str(PAYMENT_FAILED_MESSAGE)
    assert text["paid_cannot_cancel_message"] == str(PAID_CANNOT_CANCEL_MESSAGE)


def test_the_online_payment_method_does_not_reuse_the_khata_label() -> None:
    assert payment_method_label(Order.PaymentMethod.ONLINE) != payment_method_label(
        Order.PaymentMethod.CREDIT
    )
