"""The printable PDF of one GST tax invoice (D80).

reportlab draws the page with its two built-in Helvetica faces. Those faces
have no Devanagari and no rupee sign, so ``pdf_text`` is the single helper that
makes any value safe for them: a character they cannot draw becomes "?". Prices
print as ``Rs. 123.45`` for the same reason.

Nothing here says anything about how a payment is made, so no page claims that
UPI is free.
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from django.http import HttpResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.orders.models import Invoice, InvoiceLine

# Every invoice carries the same closing sentence.
COMPUTER_GENERATED_NOTE = "This is a computer-generated invoice"
TAX_INVOICE_TITLE = "TAX INVOICE"
CANCELLED_TITLE = "Cancelled"

# The table headings, in print order.
LINE_HEADINGS = ("Description", "HSN", "Qty", "Rate", "Taxable", "CGST", "SGST", "Total")
SUMMARY_HEADINGS = ("GST rate", "Taxable", "CGST", "SGST")

# Each column's width in millimetres: 174 mm of usable width on A4.
LINE_WIDTHS = [48 * mm, 14 * mm, 14 * mm, 20 * mm, 22 * mm, 17 * mm, 17 * mm, 22 * mm]
SUMMARY_WIDTHS = [30 * mm, 36 * mm, 36 * mm, 36 * mm]

ZERO = Decimal("0.00")


def pdf_text(value: object) -> str:
    """Return a value as printable ASCII, with "?" for anything else.

    This is the one place the project decides what a built-in PDF font can
    draw. Devanagari product names and the rupee sign both come out as "?".
    """
    text = "" if value is None else str(value)
    # Only the printable ASCII range survives; every other character becomes
    # "?" so the page never shows a missing-glyph box.
    return "".join(char if 32 <= ord(char) < 127 else "?" for char in text)


def money_text(value: Decimal) -> str:
    """One amount as ``Rs. 123.45``."""
    return f"Rs. {value:.2f}"


def quantity_text(value: Decimal) -> str:
    """A quantity without trailing zeros, so 2.000 prints as 2 and 1.234 stays.

    ``normalize()`` drops the trailing zeros (a ``Decimal`` keeps them by
    itself), and the ``f`` format keeps a whole number out of scientific
    notation, so 1000.000 prints as 1000 rather than 1E+3.
    """
    return f"{value.normalize():f}"


def _paragraph(text: object, style: Any) -> Paragraph:
    """One paragraph of plain text.

    reportlab reads a little XML in a paragraph, so the text is escaped after
    it has been made safe: a shop name holding ``&`` would break the page.
    """
    return Paragraph(escape(pdf_text(text)), style)


def _line_rows(lines: list[InvoiceLine]) -> list[list[str]]:
    """The goods table: one row per line, its headings first."""
    rows = [list(LINE_HEADINGS)]
    for line in lines:
        rows.append(
            [
                pdf_text(line.description),
                pdf_text(line.hsn_code),
                quantity_text(line.quantity),
                money_text(line.rate),
                money_text(line.taxable_value),
                money_text(line.cgst),
                money_text(line.sgst),
                money_text(line.line_total),
            ]
        )
    return rows


def _summary_rows(lines: list[InvoiceLine]) -> list[list[str]]:
    """The GST summary, one row per distinct GST rate, smallest rate first."""
    buckets: dict[Decimal, list[Decimal]] = {}
    for line in lines:
        # ``setdefault`` creates the three running totals on first use.
        totals = buckets.setdefault(line.gst_rate_percent, [ZERO, ZERO, ZERO])
        totals[0] += line.taxable_value
        totals[1] += line.cgst
        totals[2] += line.sgst
    rows = [list(SUMMARY_HEADINGS)]
    for rate, totals in sorted(buckets.items()):
        rows.append(
            [
                # The same "no trailing zeros" rule as a quantity, so 5.00
                # prints as 5%.
                f"{quantity_text(rate)}%",
                money_text(totals[0]),
                money_text(totals[1]),
                money_text(totals[2]),
            ]
        )
    return rows


def _styled_table(data: list[list[str]], widths: list[float]) -> Table:
    """A bordered table with a darker heading row and small type."""
    table = Table(data, colWidths=widths)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5e7eb")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#6b7280")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
            ]
        )
    )
    return table


def invoice_pdf_response(invoice: Invoice) -> HttpResponse:
    """The invoice as a PDF response, shown in the browser's own viewer.

    Both the staff screen and the customer page use this, so the content type
    and the file name are decided in one place.
    """
    response = HttpResponse(build_invoice_pdf(invoice), content_type="application/pdf")
    # The number holds slashes, so the file name uses dashes instead.
    filename = pdf_text(invoice.invoice_number).replace("/", "-")
    response["Content-Disposition"] = f'inline; filename="invoice-{filename}.pdf"'
    return response


def build_invoice_pdf(
    invoice: Invoice,
    lines: list[InvoiceLine] | None = None,
) -> bytes:
    """Return the whole invoice as PDF bytes (D80).

    ``lines`` is read from the invoice itself when it is not passed in, so a
    caller only has to hand over the invoice row.
    """
    rows = list(lines) if lines is not None else list(invoice.lines.all())
    styles = getSampleStyleSheet()
    story: list[Any] = []

    # The seller block.
    story.append(_paragraph(invoice.seller_name, styles["Title"]))
    for part in invoice.seller_address.splitlines():
        story.append(_paragraph(part, styles["Normal"]))
    story.append(_paragraph(f"GSTIN: {invoice.seller_gstin}", styles["Normal"]))
    story.append(
        _paragraph(
            f"State: {invoice.seller_state} ({invoice.seller_state_code})",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 5 * mm))
    story.append(_paragraph(TAX_INVOICE_TITLE, styles["Heading2"]))

    # The number, the date and who it is for.
    story.append(_paragraph(f"Invoice number: {invoice.invoice_number}", styles["Normal"]))
    story.append(
        _paragraph(
            f"Invoice date: {invoice.issued_at:%d %b %Y, %H:%M}" if invoice.issued_at else "",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 2 * mm))
    story.append(_paragraph("Buyer", styles["Heading3"]))
    story.append(_paragraph(invoice.buyer_name, styles["Normal"]))
    story.append(_paragraph(invoice.buyer_phone, styles["Normal"]))
    for part in invoice.buyer_address.splitlines():
        story.append(_paragraph(part, styles["Normal"]))
    story.append(_paragraph(f"Place of supply: {invoice.place_of_supply}", styles["Normal"]))
    story.append(Spacer(1, 4 * mm))

    # The goods and the delivery fee.
    story.append(_styled_table(_line_rows(rows), LINE_WIDTHS))
    story.append(Spacer(1, 5 * mm))

    # The GST summary by rate.
    story.append(_paragraph("GST summary", styles["Heading3"]))
    summary = _summary_rows(rows)
    if len(summary) > 1:
        story.append(_styled_table(summary, SUMMARY_WIDTHS))
        story.append(Spacer(1, 4 * mm))

    story.append(
        _paragraph(f"Grand total: {money_text(invoice.grand_total)}", styles["Heading3"]),
    )

    # A cancelled invoice keeps its number and is marked, never hidden (D80).
    if invoice.cancelled_at is not None:
        story.append(Spacer(1, 4 * mm))
        story.append(_paragraph(CANCELLED_TITLE, styles["Heading2"]))
        story.append(_paragraph(f"Reason: {invoice.cancel_reason}", styles["Normal"]))

    story.append(Spacer(1, 6 * mm))
    story.append(_paragraph(COMPUTER_GENERATED_NOTE, styles["Italic"]))

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=pdf_text(f"Invoice {invoice.invoice_number}"),
        # Compression off keeps the words readable in the file, which the PDF
        # tests rely on.
        pageCompression=0,
    )
    document.build(story)
    return buffer.getvalue()
