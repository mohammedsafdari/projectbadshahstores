"""The printable PDF of one GST tax invoice (D80, D92).

Text is drawn with reportlab's two built-in Helvetica faces, which have no
Devanagari and no rupee sign. ``pdf_text`` is the one helper that makes a value
safe for them: a character they cannot draw becomes "?". A paragraph or a
table cell that holds Devanagari is the one exception: it is drawn with a copy
of its style that names the bundled Noto Sans Devanagari face and turns
reportlab's shaping on, and its Latin runs are tagged back to the original
face. reportlab reads that switch from the style and never from a ``<font>``
tag, so the whole paragraph has to use the bundled face before the letters
join; joining needs uharfbuzz. A page with no Devanagari embeds no font file at
all. Prices still print as ``Rs. 123.45`` for the same reason.

Nothing here says anything about how a payment is made, so no page claims that
UPI is free.
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from django.conf import settings
from django.http import HttpResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.orders.models import Invoice, InvoiceLine

# Every invoice carries the same closing sentence.
COMPUTER_GENERATED_NOTE = "This is a computer-generated invoice"
TAX_INVOICE_TITLE = "TAX INVOICE"
CANCELLED_TITLE = "Cancelled"

# The table headings, in print order.
LINE_HEADINGS = (
    "Description",
    "HSN",
    "Qty",
    "Rate",
    "Disc.",
    "Taxable",
    "CGST",
    "SGST",
    "Total",
)
SUMMARY_HEADINGS = ("GST rate", "Taxable", "CGST", "SGST")

# Each column's width in millimetres: 174 mm of usable width on A4.
LINE_WIDTHS = [
    42 * mm,
    12 * mm,
    12 * mm,
    18 * mm,
    18 * mm,
    20 * mm,
    16 * mm,
    16 * mm,
    20 * mm,
]
SUMMARY_WIDTHS = [30 * mm, 36 * mm, 36 * mm, 36 * mm]

ZERO = Decimal("0.00")

# The two bundled Devanagari faces and the files they come from (D92). The face
# has no Latin letters, so it draws Devanagari runs only.
DEVANAGARI_FONT = "NotoSansDevanagari"
DEVANAGARI_BOLD_FONT = "NotoSansDevanagari-Bold"
FONT_DIRECTORY = Path(settings.BASE_DIR) / "static" / "fonts"
FONT_FILES = {
    DEVANAGARI_FONT: "NotoSansDevanagari-Regular.ttf",
    DEVANAGARI_BOLD_FONT: "NotoSansDevanagari-Bold.ttf",
}

# The Devanagari block, plus the two zero-width joiners that sit inside a
# Devanagari word.
DEVANAGARI_FIRST = 0x0900
DEVANAGARI_LAST = 0x097F
ZERO_WIDTH_JOINERS = ("\u200c", "\u200d")

# The small style a Devanagari cell uses, made on first use and then reused.
_CELL_STYLE: Any = None

# The face a Latin run is tagged with when its paragraph draws Devanagari. It is
# the reportlab default, kept in one name so the call reads clearly.
DEFAULT_LATIN_FONT = "Helvetica"

# One Noto copy of each paragraph style that has drawn Devanagari, made on first
# use and reused after that, keyed by the style's name and whether it is bold.
_NOTO_STYLES: dict[tuple[str, bool], Any] = {}


class InvoiceFontError(RuntimeError):
    """A bundled font file is missing, so a Hindi invoice cannot be drawn."""


def is_devanagari(character: str) -> bool:
    """True for one character of the Devanagari block."""
    return DEVANAGARI_FIRST <= ord(character) <= DEVANAGARI_LAST


def register_devanagari_fonts() -> None:
    """Register the two bundled faces with reportlab (D92).

    Safe to call again: a face that is already registered is skipped. A missing
    file raises an error that names its own path, so the fix is obvious.
    """
    for name, filename in FONT_FILES.items():
        path = FONT_DIRECTORY / filename
        if not path.is_file():
            raise InvoiceFontError(f"The Devanagari font file is missing: {path}")
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(path)))


def pdf_text(value: object) -> str:
    """Return a value as printable text, keeping Devanagari (D92).

    This is the one place the project decides what a PDF can print. Devanagari
    characters and the two zero-width joiners inside a Devanagari word are
    kept, because the bundled face can draw them. Everything else outside
    printable ASCII becomes "?", so the rupee sign still prints as "Rs." and a
    built-in face is never asked for a glyph it does not have.
    """
    text = "" if value is None else str(value)
    kept: list[str] = []
    for character in text:
        if is_devanagari(character):
            kept.append(character)
        elif character in ZERO_WIDTH_JOINERS and kept and is_devanagari(kept[-1]):
            # A joiner belongs to the word only when a Devanagari letter comes
            # before it; anywhere else Helvetica cannot draw it either.
            kept.append(character)
        elif 32 <= ord(character) < 127:
            kept.append(character)
        else:
            kept.append("?")
    return "".join(kept)


def _has_devanagari(text: str) -> bool:
    """True when the text holds at least one Devanagari character."""
    return any(is_devanagari(character) for character in text)


def _split_runs(text: str) -> list[tuple[str, bool]]:
    """Split safe text into (piece, is_devanagari) runs, in order.

    Neighbouring characters of the same kind form one run, so a single
    ``<font>`` tag wraps each Devanagari word.
    """
    runs: list[tuple[str, bool]] = []
    for character in text:
        in_run = is_devanagari(character) or character in ZERO_WIDTH_JOINERS
        if runs and runs[-1][1] == in_run:
            # A tuple cannot be changed in place, so the last run is replaced
            # by a new, longer one.
            runs[-1] = (runs[-1][0] + character, in_run)
        else:
            runs.append((character, in_run))
    return runs


def _markup(text: str, latin_font: str = DEFAULT_LATIN_FONT) -> str:
    """Escape safe text and tag its Latin runs with the drawing font (D92).

    reportlab reads a little XML inside a paragraph, so every piece is escaped
    first: a name holding ``&``, ``<`` or ``>`` cannot break the page. A
    paragraph that holds Devanagari is drawn in the bundled face, so the runs
    that are **not** Devanagari carry the original face in a ``<font>`` tag and
    the Devanagari runs stay bare. Text with no Devanagari comes out as plain
    escaped text, exactly as it did before.
    """
    if not _has_devanagari(text):
        return escape(text)
    pieces: list[str] = []
    for piece, in_devanagari in _split_runs(text):
        if in_devanagari:
            pieces.append(escape(piece))
        else:
            pieces.append(f'<font name="{latin_font}">{escape(piece)}</font>')
    return "".join(pieces)


def _noto_style(style: Any) -> Any:
    """A copy of the style that draws Devanagari with shaping on (D92).

    reportlab decides shaping from the paragraph style's own ``fontName``, not
    from the ``<font>`` tags inside the text, so a Devanagari paragraph has to
    name the bundled face itself; otherwise the vowel sign ि prints on the
    wrong side of its letter and a conjunct such as क्ष stays unjoined. The
    style passed in is never changed: it belongs to the shared sample style
    sheet, and one Hindi invoice must not turn the next English one Hindi. The
    copy keeps the original size, leading and colour.
    """
    bold = str(style.fontName).endswith("Bold")
    # A tuple key, and the copy is made once per style name and weight.
    key = (str(style.name), bold)
    replacement: Any = _NOTO_STYLES.get(key)
    if replacement is None:
        name = "InvoiceNoto" + ("Bold" if bold else "Regular")
        replacement = style.clone(name)
        replacement.fontName = DEVANAGARI_BOLD_FONT if bold else DEVANAGARI_FONT
        # The switch reportlab reads before it shapes a paragraph.
        replacement.shaping = 1
        _NOTO_STYLES[key] = replacement
    return replacement


def _cell_style() -> Any:
    """The small style a Devanagari table cell uses, made once."""
    global _CELL_STYLE
    if _CELL_STYLE is None:
        style = getSampleStyleSheet()["Normal"].clone("InvoiceCell")
        style.fontSize = 8
        style.leading = 10
        _CELL_STYLE = style
    return _CELL_STYLE


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
    """One paragraph of text, drawn in the bundled face when it is Hindi.

    reportlab reads a little XML in a paragraph, so the text is escaped after
    it has been made safe: a shop name holding ``&`` would break the page.
    """
    safe = pdf_text(text)
    if not _has_devanagari(safe):
        return Paragraph(_markup(safe), style)
    # Registering here is what keeps an English invoice working even when the
    # font files are missing (D92).
    register_devanagari_fonts()
    return Paragraph(_markup(safe, style.fontName), _noto_style(style))


def _cell(value: object) -> str | Paragraph:
    """One table cell.

    A plain string cell cannot switch font, so a cell holding Devanagari is a
    Paragraph drawn in the bundled face; a cell of only ASCII stays a plain
    string, which leaves an English invoice exactly as it was (D92).
    """
    text = pdf_text(value)
    if not _has_devanagari(text):
        return text
    register_devanagari_fonts()
    style = _cell_style()
    return Paragraph(_markup(text, style.fontName), _noto_style(style))


def _line_rows(lines: list[InvoiceLine]) -> list[list[Any]]:
    """The goods table: one row per line, its headings first."""
    rows: list[list[Any]] = [list(LINE_HEADINGS)]
    for line in lines:
        rows.append(
            [
                _cell(line.description),
                pdf_text(line.hsn_code),
                quantity_text(line.quantity),
                money_text(line.rate),
                money_text(line.discount_amount) if line.discount_amount > ZERO else "",
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


def _styled_table(data: list[list[Any]], widths: list[float]) -> Table:
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

    # A discounted invoice names the code in its totals; an invoice without a
    # discount prints exactly as it did before (D82).
    if invoice.discount_total > ZERO:
        label = f"Discount ({invoice.coupon_code})" if invoice.coupon_code else "Discount"
        story.append(
            _paragraph(f"{label}: -{money_text(invoice.discount_total)}", styles["Normal"]),
        )
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
