"""Tests for the "My orders" wording helpers (D70)."""

from __future__ import annotations

from datetime import UTC, datetime

from apps.orders.presentation import item_count_text, order_date_text, page_label, page_text

MY_ORDERS_KEYS = (
    "my_orders_title",
    "my_orders_heading",
    "no_orders_message",
    "start_shopping_link",
    "placed_on_label",
    "view_order_link",
    "previous_link",
    "next_link",
)


def test_order_date_text_uses_the_shop_time_zone() -> None:
    # 20:00 UTC is already 01:30 on the next day in India (UTC+5:30).
    late_utc = datetime(2026, 11, 7, 20, 0, tzinfo=UTC)
    early_utc = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)

    assert order_date_text(late_utc) == "8 Nov 2026"
    # A single-digit day has no leading zero.
    assert order_date_text(early_utc) == "3 Nov 2026"


def test_item_count_text_is_singular_for_one() -> None:
    assert item_count_text(1) == "1 item"


def test_item_count_text_is_plural_otherwise() -> None:
    assert item_count_text(0) == "0 items"
    assert item_count_text(3) == "3 items"


def test_page_label_has_both_numbers() -> None:
    assert page_label(2, 5) == "Page 2 of 5"


def test_page_text_has_the_my_orders_sentences() -> None:
    text = page_text()

    for key in MY_ORDERS_KEYS:
        assert key in text
        assert text[key].strip()
