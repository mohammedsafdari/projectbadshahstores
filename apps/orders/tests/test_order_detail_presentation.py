"""Tests for the order detail and cancel wording (Slice 38d, D70)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from apps.orders.forms import CancelOrderForm
from apps.orders.models import Order
from apps.orders.presentation import (
    BACK_TO_ORDERS_LINK,
    CANCEL_CONFIRM_BUTTON,
    CANCEL_HEADING,
    CANCEL_HELP,
    CANCEL_ORDER_LINK,
    CANCEL_REASON_LABEL,
    CANCEL_REASON_TOO_LONG,
    CANCEL_TITLE,
    KEEP_ORDER_LINK,
    ORDER_DETAIL_HEADING,
    ORDER_DETAIL_TITLE,
    TIMELINE_HEADING,
    VIEW_THIS_ORDER_LINK,
    WHATSAPP_BUTTON,
    cancel_question,
    credit_returned_sentence,
    page_text,
    status_sentence,
    timeline_label,
    timeline_time_text,
    whatsapp_href,
)


def test_whatsapp_href_uses_digits_only_and_encodes_the_message() -> None:
    href = whatsapp_href("+91 98765 43210", "BS/26-27/5")

    message = "Hello, I have a question about my order BS/26-27/5."
    assert href == f"https://wa.me/919876543210?text={quote(message, safe='')}"
    assert "%2F" in href


def test_whatsapp_href_accepts_spaces_and_a_leading_zero() -> None:
    href = whatsapp_href("098765 43210", "BS/26-27/5")

    assert href.startswith("https://wa.me/919876543210?text=")


def test_whatsapp_href_is_blank_for_a_blank_number() -> None:
    assert whatsapp_href("", "BS/26-27/5") == ""
    assert whatsapp_href("   ", "BS/26-27/5") == ""


def test_whatsapp_href_is_blank_for_an_invalid_number() -> None:
    assert whatsapp_href("12345", "BS/26-27/5") == ""
    assert whatsapp_href("not a phone", "BS/26-27/5") == ""


def test_timeline_label_exists_for_every_status() -> None:
    for status in Order.Status.values:
        label = timeline_label(status)

        assert label
        assert "_" not in label
        assert label != status


def test_timeline_label_falls_back_for_an_unknown_status() -> None:
    assert timeline_label("NOT_A_STATUS") == status_sentence("NOT_A_STATUS")


def test_timeline_time_text_uses_the_shop_time_zone() -> None:
    moment = datetime(2026, 11, 7, 20, 0, tzinfo=ZoneInfo("UTC"))

    assert timeline_time_text(moment) == "8 Nov 2026, 01:30"


def test_credit_returned_sentence_has_the_amount() -> None:
    assert credit_returned_sentence(Decimal("230.00")) == ("₹230.00 was returned to your khata.")


def test_cancel_question_names_the_order_number() -> None:
    assert cancel_question("BS/26-27/5") == ("Are you sure you want to cancel order BS/26-27/5?")


def test_page_text_has_the_order_detail_and_cancel_sentences() -> None:
    text = page_text()

    assert text["order_detail_title"] == str(ORDER_DETAIL_TITLE)
    assert text["order_detail_heading"] == str(ORDER_DETAIL_HEADING)
    assert text["timeline_heading"] == str(TIMELINE_HEADING)
    assert text["back_to_orders_link"] == str(BACK_TO_ORDERS_LINK)
    assert text["view_this_order_link"] == str(VIEW_THIS_ORDER_LINK)
    assert text["cancel_order_link"] == str(CANCEL_ORDER_LINK)
    assert text["cancel_title"] == str(CANCEL_TITLE)
    assert text["cancel_heading"] == str(CANCEL_HEADING)
    assert text["cancel_help"] == str(CANCEL_HELP)
    assert text["cancel_reason_label"] == str(CANCEL_REASON_LABEL)
    assert text["cancel_confirm_button"] == str(CANCEL_CONFIRM_BUTTON)
    assert text["keep_order_link"] == str(KEEP_ORDER_LINK)
    assert text["whatsapp_button"] == str(WHATSAPP_BUTTON)


def test_cancel_form_accepts_a_blank_reason() -> None:
    form = CancelOrderForm({"reason": ""})

    assert form.is_valid()
    assert form.cleaned_data["reason"] == ""


def test_cancel_form_refuses_a_reason_over_200_characters() -> None:
    form = CancelOrderForm({"reason": "x" * 201})

    assert not form.is_valid()
    assert str(CANCEL_REASON_TOO_LONG) in form.errors["reason"]


def test_cancel_form_strips_the_reason() -> None:
    form = CancelOrderForm({"reason": "  Changed my mind  "})

    assert form.is_valid()
    assert form.cleaned_data["reason"] == "Changed my mind"
