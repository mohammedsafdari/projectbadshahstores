"""The reports must agree with each other and with the invoices (D85)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.orders.invoices import mark_invoice_cancelled
from apps.orders.models import Invoice, InvoiceLine
from apps.orders.tests.staff_helpers import NOW, staff
from apps.reports.queries import (
    GST_NOTE,
    SALES_KEYS,
    DateRange,
    gst_report,
    sales_report,
)
from apps.reports.tests.helpers import FIRST_DAY, LAST_DAY, make_variant, sold, staff_client

pytestmark = pytest.mark.django_db

NOVEMBER = DateRange(FIRST_DAY, LAST_DAY)
PARAMS = {"from": FIRST_DAY.isoformat(), "to": LAST_DAY.isoformat()}


def test_the_four_sales_sections_agree_with_the_live_invoices() -> None:
    sold(lines=[(make_variant(name="Atta", gst="5.00"), "2")])
    sold(lines=[(make_variant(name="Soap", gst="18.00"), "3")])
    cancelled_order, _invoice = sold(lines=[(make_variant(name="Rice"), "1")])
    mark_invoice_cancelled(cancelled_order, "Out of stock", NOW)
    actor = staff()

    totals = {key: sales_report(actor, NOVEMBER, key).totals.total for key in SALES_KEYS}
    fee_total = sum(
        (
            line.line_total
            for line in InvoiceLine.objects.filter(
                is_fee_line=True,
                invoice__cancelled_at__isnull=True,
            )
        ),
        Decimal("0.00"),
    )
    invoices = sum(
        (invoice.grand_total for invoice in Invoice.objects.filter(cancelled_at__isnull=True)),
        Decimal("0.00"),
    )

    assert len(set(totals.values())) == 1
    assert totals[SALES_KEYS[0]] == invoices - fee_total
    assert totals[SALES_KEYS[0]] > Decimal("0.00")


def test_the_gst_total_is_the_invoice_gst_on_the_page_and_in_the_csv() -> None:
    sold(lines=[(make_variant(name="Atta", gst="5.00"), "2")])
    sold(lines=[(make_variant(name="Soap", gst="18.00"), "1")])
    report = gst_report(staff(), NOVEMBER)
    printed = sum(
        (invoice.gst_total for invoice in Invoice.objects.filter(cancelled_at__isnull=True)),
        Decimal("0.00"),
    )

    page = staff_client().get(reverse("reports_gst"), PARAMS)
    csv_text = staff_client().get(reverse("reports_gst_csv"), PARAMS).content.decode("utf-8-sig")

    assert report.gst == printed
    assert GST_NOTE in page.content.decode()
    assert f"Total,,{report.taxable},{report.cgst},{report.sgst},{report.gst}," in csv_text
