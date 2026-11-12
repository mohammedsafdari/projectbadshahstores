"""Tests for the invoice PDF and its one text helper (D80)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.orders.invoice_pdf import (
    SUMMARY_HEADINGS,
    _summary_rows,
    build_invoice_pdf,
    money_text,
    pdf_text,
    quantity_text,
)
from apps.orders.tests.invoice_helpers import gst_variant, sent_out_order

pytestmark = pytest.mark.django_db


def test_the_helper_replaces_devanagari_with_a_question_mark() -> None:
    text = pdf_text("दूध 500 g")

    # Three Devanagari letters, so three question marks before the ASCII part.
    assert text.startswith("???")
    assert text.endswith(" 500 g")
    assert all(ord(char) < 128 for char in text)


def test_the_helper_keeps_plain_ascii() -> None:
    assert pdf_text("Badshah Store, Indore") == "Badshah Store, Indore"
    assert pdf_text(None) == ""
    assert pdf_text(12) == "12"


def test_rs_is_used_and_the_rupee_sign_is_not() -> None:
    _order, invoice = sent_out_order()

    content = build_invoice_pdf(invoice)

    assert b"Rs." in content
    assert "\u20b9".encode() not in content


def test_an_amount_appears_in_the_pdf_text() -> None:
    _order, invoice = sent_out_order()

    content = build_invoice_pdf(invoice)

    assert money_text(invoice.grand_total).encode() in content


def test_the_pdf_has_a_header_and_a_body() -> None:
    _order, invoice = sent_out_order()

    content = build_invoice_pdf(invoice)

    assert content.startswith(b"%PDF")
    assert content.rstrip().endswith(b"%%EOF")
    assert len(content) > 1000


def test_the_computer_generated_sentence_is_printed() -> None:
    _order, invoice = sent_out_order()

    content = build_invoice_pdf(invoice)

    assert b"This is a computer-generated invoice" in content


def test_the_gst_summary_names_every_rate() -> None:
    first = gst_variant(price="95.00", gst="5.00")
    second = gst_variant(price="41.50", gst="12.00")
    _order, invoice = sent_out_order(lines=[(first, "2"), (second, "3")])

    content = build_invoice_pdf(invoice)

    assert b"5%" in content
    assert b"12%" in content


def test_the_goods_table_prints_the_hsn_code() -> None:
    row = gst_variant(hsn="19053100")
    _order, invoice = sent_out_order(lines=[(row, "2")])

    content = build_invoice_pdf(invoice)

    # The HSN column must hold the code, not fall back to the description.
    assert b"19053100" in content


def test_the_gst_summary_adds_up_each_rate() -> None:
    first = gst_variant(price="95.00", gst="5.00")
    second = gst_variant(price="95.00", gst="5.00")
    _order, invoice = sent_out_order(
        lines=[(first, "2"), (second, "2")],
        delivery_fee="0.00",
    )

    # The rows are read directly: counting amounts inside the PDF bytes would
    # match the wrong column as soon as two columns hold the same number.
    rows = _summary_rows(list(invoice.lines.all()))

    # Two lines of 190.00 at 5%: 361.90 taxable, 9.06 CGST and 9.04 SGST.
    assert rows == [
        list(SUMMARY_HEADINGS),
        ["5%", "Rs. 361.90", "Rs. 9.06", "Rs. 9.04"],
    ]


def test_the_place_of_supply_is_printed() -> None:
    _order, invoice = sent_out_order()

    content = build_invoice_pdf(invoice)

    assert b"Place of supply" in content


def test_a_quantity_loses_its_trailing_zeros() -> None:
    assert quantity_text(Decimal("2.000")) == "2"
    assert quantity_text(Decimal("1.234")) == "1.234"
    assert money_text(Decimal("15.2")) == "Rs. 15.20"
