"""CSV downloads of the staff reports (D85), written with the D46 rules.

Every file starts with a UTF-8 byte order mark, so Excel on Windows reads Hindi
text and the rupee sign correctly; a reader in Python must open it with
``encoding="utf-8-sig"``. Numbers are plain digits (never scientific notation
and never a thousands separator), and a text cell that starts with a character a
spreadsheet treats as a formula gets a leading single quote, so a product name
such as ``=HYPERLINK(...)`` can never run.

This module does no HTTP work: it writes into any file-like object, so a view
passes a response and a test passes an ``io.StringIO``.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Protocol

from apps.reports.queries import (
    CodCreditReport,
    GstReport,
    GstRow,
    OwnBrandReport,
    SalesReport,
    ValuationReport,
)

# The first character of the file: Excel on Windows uses it to recognise UTF-8.
UTF8_BOM = "\ufeff"

# Excel and Google Sheets treat a cell that starts with one of these as a formula.
FORMULA_STARTERS = ("=", "+", "-", "@", "\t", "\r")

# Quantity cells keep three decimals, because a loose item is sold by weight.
QUANTITY_PLACES = Decimal("0.001")


class SupportsWrite(Protocol):
    """Anything with a ``write(text)`` method: an open file, a StringIO or a response."""

    def write(self, s: str, /) -> object: ...


def safe_text(value: str) -> str:
    """Stop a text cell from being run as a spreadsheet formula (D46 point 5)."""
    if value.startswith(FORMULA_STARTERS):
        return "'" + value
    return value


def money_text(value: Decimal) -> str:
    """A Decimal as plain digits, e.g. ``95.00``; ``format`` never uses 1E+2."""
    return format(value, "f")


def quantity_text(value: Decimal) -> str:
    """A quantity with its three decimals, e.g. ``2.500``."""
    return format(value.quantize(QUANTITY_PLACES), "f")


def write_rows(file: SupportsWrite, header: Sequence[str], rows: Iterable[Sequence[str]]) -> int:
    """Write the header and every row, and return how many rows were written."""
    file.write(UTF8_BOM)
    writer = csv.writer(file)
    writer.writerow(list(header))
    count = 0
    for row in rows:
        writer.writerow(list(row))
        count += 1
    return count


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------
SALES_HEADER = ("section", "label", "units", "taxable_value", "gst", "total")


def write_sales_csv(reports: list[SalesReport], file: SupportsWrite) -> int:
    """All four sales sections in one file, one ``section`` column telling them apart."""
    rows: list[list[str]] = []
    for report in reports:
        for row in report.rows:
            rows.append(
                [
                    report.heading,
                    safe_text(row.label),
                    quantity_text(row.totals.units),
                    money_text(row.totals.taxable),
                    money_text(row.totals.gst),
                    money_text(row.totals.total),
                ]
            )
        rows.append(
            [
                report.heading,
                "Total",
                quantity_text(report.totals.units),
                money_text(report.totals.taxable),
                money_text(report.totals.gst),
                money_text(report.totals.total),
            ]
        )
        # The cancelled invoices are shown, and they are never added to a total.
        rows.append(
            [
                report.heading,
                safe_text(f"Cancelled invoices (not in the totals): {report.cancelled_count}"),
                "",
                "",
                "",
                money_text(report.cancelled_total),
            ]
        )
    return write_rows(file, SALES_HEADER, rows)


# ---------------------------------------------------------------------------
# Own brand
# ---------------------------------------------------------------------------
OWN_BRAND_HEADER = ("group", "revenue_taxable", "cost_at_todays_cost", "margin", "margin_percent")


def write_own_brand_csv(report: OwnBrandReport, file: SupportsWrite) -> int:
    """The two margin groups, then the variants that have no cost price."""
    rows: list[list[str]] = []
    for group in (report.own_brand, report.third_party):
        rows.append(
            [
                safe_text(group.label),
                money_text(group.revenue),
                money_text(group.cost),
                money_text(group.margin),
                money_text(group.margin_percent),
            ]
        )
    for variant in report.blank_cost_variants:
        rows.append(
            [safe_text(f"No cost price: {variant.sku_code} {variant.name}"), "", "", "", ""]
        )
    return write_rows(file, OWN_BRAND_HEADER, rows)


# ---------------------------------------------------------------------------
# GST
# ---------------------------------------------------------------------------
GST_HEADER = (
    "row",
    "gst_rate_percent",
    "taxable_value",
    "cgst",
    "sgst",
    "total_gst",
    "invoice_count",
)


def _gst_row(kind: str, row: GstRow) -> list[str]:
    """One CSV row of the GST summary, with its own kind in the first cell."""
    return [
        kind,
        money_text(row.rate),
        money_text(row.taxable),
        money_text(row.cgst),
        money_text(row.sgst),
        money_text(row.gst),
        str(row.invoice_count),
    ]


def write_gst_csv(report: GstReport, file: SupportsWrite) -> int:
    """The GST summary, with the unconfirmed note as the first comment row."""
    file.write(UTF8_BOM)
    writer = csv.writer(file)
    # The one-cell note row comes before the header, so a person who opens the
    # file reads it first (D85).
    writer.writerow([safe_text(report.note)])
    writer.writerow(list(GST_HEADER))
    rows: list[list[str]] = []
    for row in report.rows:
        rows.append(_gst_row("Goods", row))
    for row in report.fee_rows:
        rows.append(_gst_row("Delivery fee", row))
    rows.append(
        [
            "Total",
            "",
            money_text(report.taxable),
            money_text(report.cgst),
            money_text(report.sgst),
            money_text(report.gst),
            "",
        ]
    )
    rows.append(
        [
            safe_text(f"Cancelled invoices (not in the totals): {report.cancelled_count}"),
            "",
            "",
            "",
            "",
            money_text(report.cancelled_total),
            "",
        ]
    )
    count = 0
    for line in rows:
        writer.writerow(line)
        count += 1
    return count


# ---------------------------------------------------------------------------
# COD and credit
# ---------------------------------------------------------------------------
COD_CREDIT_HEADER = ("figure", "amount", "count")


def write_cod_credit_csv(report: CodCreditReport, file: SupportsWrite) -> int:
    """What the door collected, what it still owes, and the khata figures."""
    rows: list[list[str]] = [
        ["COD collected", money_text(report.cod_collected), str(report.cod_collected_count)],
        ["COD pending", money_text(report.cod_pending), str(report.cod_pending_count)],
        [
            "Khata outstanding",
            money_text(report.khata_outstanding),
            str(report.khata_account_count),
        ],
        [
            "Khata payments received",
            money_text(report.khata_payments),
            str(report.khata_payment_count),
        ],
    ]
    if report.cod_pending_note:
        rows.append([safe_text(report.cod_pending_note), "", ""])
    return write_rows(file, COD_CREDIT_HEADER, rows)


# ---------------------------------------------------------------------------
# Stock valuation
# ---------------------------------------------------------------------------
VALUATION_HEADER = (
    "row",
    "sku_code",
    "name",
    "brand",
    "category",
    "on_hand",
    "cost_price",
    "value",
)


def write_valuation_csv(report: ValuationReport, file: SupportsWrite) -> int:
    """Every valued variant, then the subtotals, the total and the blank costs."""
    rows: list[list[str]] = []
    for row in report.rows:
        rows.append(
            [
                "Variant",
                safe_text(row.sku_code),
                safe_text(row.name),
                safe_text(row.brand),
                safe_text(row.category),
                quantity_text(row.on_hand),
                money_text(row.cost_price),
                money_text(row.value),
            ]
        )
    for group in report.brand_totals:
        rows.append(
            ["Brand total", "", "", safe_text(group.label), "", "", "", money_text(group.value)]
        )
    for group in report.category_totals:
        rows.append(
            ["Category total", "", "", "", safe_text(group.label), "", "", money_text(group.value)]
        )
    rows.append(["Grand total", "", "", "", "", "", "", money_text(report.grand_total)])
    for variant in report.blank_cost_variants:
        rows.append(
            [
                "No cost price",
                safe_text(variant.sku_code),
                safe_text(variant.name),
                "",
                "",
                "",
                "",
                "",
            ]
        )
    return write_rows(file, VALUATION_HEADER, rows)
