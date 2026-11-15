"""The bundled Devanagari faces in the invoice PDF (D92).

An ASCII-only invoice must print exactly as before and embed no font file. A
Devanagari name or address switches only those runs to the bundled face, and
reportlab shapes the letters into conjuncts when uharfbuzz is installed.
"""

from __future__ import annotations

import importlib.util
from decimal import Decimal

import pytest
from reportlab.pdfbase import pdfmetrics

from apps.orders import invoice_pdf
from apps.orders.invoice_pdf import (
    DEVANAGARI_BOLD_FONT,
    DEVANAGARI_FONT,
    InvoiceFontError,
    build_invoice_pdf,
    money_text,
    pdf_text,
    register_devanagari_fonts,
)
from apps.orders.tests.invoice_helpers import sent_out_order

pytestmark = pytest.mark.django_db

# Two Hindi words, a conjunct and a joiner: "milk" and "ksha" plus a zero-width
# joiner between two letters.
HINDI_WORD = "दूध"


def test_pdf_text_keeps_devanagari_and_zero_width_joiners() -> None:
    assert pdf_text(f"{HINDI_WORD} 500 g") == f"{HINDI_WORD} 500 g"
    assert pdf_text("क्\u200dष") == "क्\u200dष"
    assert pdf_text("क्\u200cष") == "क्\u200cष"
    # A joiner on its own is not part of a Devanagari word, so the built-in
    # face still cannot draw it.
    assert pdf_text("\u200dabc") == "?abc"


def test_pdf_text_still_replaces_the_rupee_sign_and_other_symbols() -> None:
    assert pdf_text("₹ 12") == "? 12"
    assert pdf_text("café") == "caf?"
    assert pdf_text("—") == "?"
    assert pdf_text(None) == ""


def test_an_ascii_only_invoice_does_not_embed_the_noto_font() -> None:
    _order, invoice = sent_out_order()

    content = build_invoice_pdf(invoice)

    # Helvetica is never embedded; a TrueType file would appear as FontFile2.
    assert b"FontFile2" not in content
    assert DEVANAGARI_FONT.encode() not in content


def test_an_invoice_with_a_hindi_address_embeds_the_noto_font() -> None:
    _order, invoice = sent_out_order(ship_line1=f"{HINDI_WORD} गली 12")

    content = build_invoice_pdf(invoice)

    assert DEVANAGARI_FONT.encode() in content


def test_an_invoice_with_a_hindi_customer_name_builds_a_pdf() -> None:
    _order, invoice = sent_out_order(ship_name="पिंकी शर्मा")

    content = build_invoice_pdf(invoice)

    assert content.startswith(b"%PDF")
    assert content.rstrip().endswith(b"%%EOF")


def test_a_hindi_name_with_an_ampersand_and_angle_brackets_still_builds() -> None:
    _order, invoice = sent_out_order(ship_name="दूध & <तेल>")

    content = build_invoice_pdf(invoice)

    assert content.startswith(b"%PDF")
    assert b"This is a computer-generated invoice" in content


def test_a_mixed_hindi_and_english_line_builds() -> None:
    _order, invoice = sent_out_order()
    line = invoice.lines.first()
    assert line is not None
    line.description = f"{HINDI_WORD} Milk 500 g"
    line.save(update_fields=["description"])

    content = build_invoice_pdf(invoice)

    assert DEVANAGARI_FONT.encode() in content
    assert b"Milk" in content


def test_the_registered_devanagari_font_is_shapable() -> None:
    register_devanagari_fonts()

    font = pdfmetrics.getFont(DEVANAGARI_FONT)

    # reportlab reports True only when uharfbuzz is installed, which is what
    # joins a Devanagari conjunct such as क्ष or त्र.
    assert font.shapable is True


def test_uharfbuzz_is_installed() -> None:
    # find_spec looks the package up without importing it, so mypy needs no
    # stubs for it.
    assert importlib.util.find_spec("uharfbuzz") is not None


def test_registering_the_fonts_twice_is_safe() -> None:
    register_devanagari_fonts()
    register_devanagari_fonts()

    names = pdfmetrics.getRegisteredFontNames()

    assert names.count(DEVANAGARI_FONT) == 1
    assert names.count(DEVANAGARI_BOLD_FONT) == 1


def test_a_missing_font_file_names_its_path_in_the_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(invoice_pdf, "FONT_DIRECTORY", tmp_path)

    with pytest.raises(InvoiceFontError) as raised:
        register_devanagari_fonts()

    assert "NotoSansDevanagari-Regular.ttf" in str(raised.value)
    assert str(tmp_path) in str(raised.value)


def test_the_bold_face_is_registered_too() -> None:
    register_devanagari_fonts()

    assert DEVANAGARI_BOLD_FONT in pdfmetrics.getRegisteredFontNames()


def test_money_still_prints_as_rs() -> None:
    assert money_text(Decimal("123.45")) == "Rs. 123.45"
    assert pdf_text(money_text(Decimal("123.45"))) == "Rs. 123.45"
    assert pdf_text("₹123.45") == "?123.45"
