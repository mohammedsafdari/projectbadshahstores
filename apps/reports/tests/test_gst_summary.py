"""The GST summary by rate, from the frozen invoice lines (D85)."""

from __future__ import annotations

import csv
import io
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.orders.invoices import mark_invoice_cancelled
from apps.orders.models import Invoice
from apps.orders.tests.staff_helpers import NOW, staff
from apps.reports.queries import GST_NOTE, DateRange, ReportsPermissionError, gst_report
from apps.reports.tests.helpers import FIRST_DAY, LAST_DAY, make_variant, sold, staff_client

pytestmark = pytest.mark.django_db

NOVEMBER = DateRange(FIRST_DAY, LAST_DAY)


def _invoices_gst() -> Decimal:
    total = Decimal("0.00")
    for invoice in Invoice.objects.filter(cancelled_at__isnull=True):
        total += invoice.gst_total
    return total


def test_one_row_per_goods_rate() -> None:
    sold(lines=[(make_variant(name="Atta", gst="5.00"), "2")])
    sold(lines=[(make_variant(name="Soap", gst="18.00"), "1")])

    report = gst_report(staff(), NOVEMBER)

    assert [row.rate for row in report.rows] == [Decimal("5.00"), Decimal("18.00")]
    assert all(row.is_fee_line is False for row in report.rows)


def test_the_two_halves_add_up_to_the_row_gst() -> None:
    sold(lines=[(make_variant(gst="5.00"), "2")])

    report = gst_report(staff(), NOVEMBER)
    row = report.rows[0]

    assert row.gst == row.cgst + row.sgst
    assert row.taxable > Decimal("0.00")


def test_the_delivery_fee_is_its_own_row() -> None:
    _order, invoice = sold(lines=[(make_variant(gst="5.00"), "2")])
    fee = invoice.lines.get(is_fee_line=True)

    report = gst_report(staff(), NOVEMBER)

    assert len(report.fee_rows) == 1
    assert report.fee_rows[0].is_fee_line is True
    assert report.fee_rows[0].rate == fee.gst_rate_percent
    assert report.fee_rows[0].gst == fee.cgst + fee.sgst
    # The fee row is never inside the goods rows.
    assert all(row.is_fee_line is False for row in report.rows)


def test_the_invoice_count_of_a_rate_counts_its_invoices_once() -> None:
    variant = make_variant(gst="5.00")
    sold(lines=[(variant, "1")])
    sold(lines=[(variant, "2")])

    report = gst_report(staff(), NOVEMBER)

    goods_row = report.rows[0]
    assert goods_row.invoice_count == 2


def test_the_total_gst_is_the_sum_of_the_invoices_own_gst() -> None:
    sold(lines=[(make_variant(name="Atta", gst="5.00"), "2")])
    sold(lines=[(make_variant(name="Soap", gst="18.00"), "1")])

    report = gst_report(staff(), NOVEMBER)

    assert report.gst == _invoices_gst()
    assert report.gst == report.cgst + report.sgst


def test_taxable_plus_gst_is_the_sum_of_the_invoice_totals() -> None:
    sold(lines=[(make_variant(name="Atta", gst="5.00"), "2")])
    sold(lines=[(make_variant(name="Soap", gst="18.00"), "1")])
    printed = sum(
        (invoice.grand_total for invoice in Invoice.objects.filter(cancelled_at__isnull=True)),
        Decimal("0.00"),
    )

    report = gst_report(staff(), NOVEMBER)

    assert report.taxable + report.gst == printed


def test_a_cancelled_invoice_is_left_out_and_shown() -> None:
    order, invoice = sold(lines=[(make_variant(gst="5.00"), "2")])
    mark_invoice_cancelled(order, "Returned", NOW)

    report = gst_report(staff(), NOVEMBER)

    assert report.rows == []
    assert report.gst == Decimal("0.00")
    assert report.cancelled_count == 1
    assert report.cancelled_total == invoice.grand_total


def test_the_note_is_on_the_page() -> None:
    response = staff_client().get(
        reverse("reports_gst"),
        {"from": FIRST_DAY.isoformat(), "to": LAST_DAY.isoformat()},
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert GST_NOTE in html


def test_the_note_is_the_first_row_of_the_csv() -> None:
    response = staff_client().get(
        reverse("reports_gst_csv"),
        {"from": FIRST_DAY.isoformat(), "to": LAST_DAY.isoformat()},
    )
    text = response.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))

    # csv.reader undoes the quoting a field with commas in it gets.
    assert rows[0] == [GST_NOTE]


def test_a_user_without_the_capability_cannot_read_the_gst_report() -> None:
    with pytest.raises(ReportsPermissionError):
        gst_report(staff("PACKER"), NOVEMBER)
