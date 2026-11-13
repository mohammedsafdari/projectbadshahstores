"""Tests for the coupon sentences (D83).

Every customer sentence lives in ``apps/orders/presentation.py``, is wrapped
for translation, and is filled by a small function rather than in a template.
"""

from __future__ import annotations

from decimal import Decimal

from django.utils.functional import Promise

from apps.orders import presentation


def test_the_fixed_coupon_sentences_are_in_page_text() -> None:
    text = presentation.page_text()

    assert text["coupon_heading"] == "Have a coupon?"
    assert text["coupon_code_label"] == "Coupon code"
    assert text["coupon_apply_button"] == "Apply"
    assert text["coupon_remove_button"] == "Remove"
    assert text["delivery_discount_label"] == "Delivery discount"
    assert text["coupon_removed_message"] == "Coupon removed."
    assert text["coupon_code_required"] == "Please type a coupon code."


def test_the_pattern_sentences_are_in_page_text() -> None:
    text = presentation.page_text()

    assert text["coupon_row_label"] == "Coupon (%(code)s)"
    assert text["coupon_applied_sentence"] == "Coupon %(code)s applied."
    assert text["coupon_saved_sentence"] == "%(code)s is applied. You save %(amount)s."


def test_the_coupon_row_label_fills_the_code() -> None:
    assert presentation.coupon_row_label("DIWALI10") == "Coupon (DIWALI10)"


def test_the_applied_sentence_fills_the_code() -> None:
    assert presentation.coupon_applied_message("DIWALI10") == "Coupon DIWALI10 applied."


def test_the_saved_sentence_fills_the_code_and_the_amount() -> None:
    assert presentation.coupon_saved_message("DIWALI10", Decimal("20.00")) == (
        "DIWALI10 is applied. You save ₹20.00."
    )


def test_the_coupon_sentences_are_marked_for_translation() -> None:
    sentences = (
        presentation.COUPON_HEADING,
        presentation.COUPON_CODE_LABEL,
        presentation.COUPON_APPLY_BUTTON,
        presentation.COUPON_REMOVE_BUTTON,
        presentation.COUPON_ROW_LABEL,
        presentation.DELIVERY_DISCOUNT_LABEL,
        presentation.COUPON_APPLIED_SENTENCE,
        presentation.COUPON_REMOVED_MESSAGE,
        presentation.COUPON_CODE_REQUIRED,
        presentation.COUPON_SAVED_SENTENCE,
    )

    for sentence in sentences:
        assert isinstance(sentence, Promise), sentence
