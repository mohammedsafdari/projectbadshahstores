"""The CSV downloads, written with the D46 rules (D85)."""

from __future__ import annotations

import csv
import io
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.orders.tests.staff_helpers import staff
from apps.reports.csv_output import (
    GST_HEADER,
    money_text,
    quantity_text,
    safe_text,
    write_gst_csv,
    write_rows,
    write_sales_csv,
)
from apps.reports.queries import GST_NOTE, DateRange, gst_report, sales_reports
from apps.reports.tests.helpers import FIRST_DAY, LAST_DAY, make_variant, sold, staff_client

pytestmark = pytest.mark.django_db

NOVEMBER = DateRange(FIRST_DAY, LAST_DAY)
PARAMS = {"from": FIRST_DAY.isoformat(), "to": LAST_DAY.isoformat()}


def test_the_file_starts_with_a_byte_order_mark() -> None:
    buffer = io.StringIO()
    write_rows(buffer, ["a"], [["1"]])

    assert buffer.getvalue().startswith("\ufeff")


def test_a_currency_amount_is_plain_digits() -> None:
    assert money_text(Decimal("1234.50")) == "1234.50"
    assert money_text(Decimal("0.00")) == "0.00"


def test_a_quantity_keeps_three_decimals() -> None:
    assert quantity_text(Decimal("2.5")) == "2.500"
    assert quantity_text(Decimal("12")) == "12.000"


def test_a_cell_that_looks_like_a_formula_is_guarded() -> None:
    assert safe_text("=SUM(A1)") == "'=SUM(A1)"
    assert safe_text("+91") == "'+91"
    assert safe_text("Atta") == "Atta"


def test_a_product_name_that_looks_like_a_formula_is_guarded_in_the_sales_file() -> None:
    sold(lines=[(make_variant(name="=HYPERLINK(1)"), "1")])
    buffer = io.StringIO()

    write_sales_csv(sales_reports(staff(), NOVEMBER), buffer)

    assert "'=HYPERLINK(1)" in buffer.getvalue()


def test_the_sales_file_carries_all_four_sections_and_the_cancelled_line() -> None:
    sold(lines=[(make_variant(), "2")])
    buffer = io.StringIO()

    write_sales_csv(sales_reports(staff(), NOVEMBER), buffer)

    text = buffer.getvalue()
    for heading in ("Day", "Product", "Brand", "Category"):
        assert heading in text
    assert "Cancelled invoices (not in the totals)" in text


def test_the_gst_file_starts_with_the_note() -> None:
    sold(lines=[(make_variant(), "1")])
    buffer = io.StringIO()

    write_gst_csv(gst_report(staff(), NOVEMBER), buffer)

    # csv.reader undoes the quoting a field with commas in it gets.
    rows = list(csv.reader(io.StringIO(buffer.getvalue().removeprefix("\ufeff"))))
    assert rows[0] == [GST_NOTE]
    assert rows[1] == list(GST_HEADER)


def test_a_download_is_a_csv_attachment() -> None:
    response = staff_client().get(reverse("reports_sales_csv"), PARAMS)

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert "attachment" in response["Content-Disposition"]
    assert response["Content-Disposition"].endswith('.csv"')
    assert response.content.startswith(b"\xef\xbb\xbf")


def test_every_report_offers_its_download() -> None:
    client = staff_client()
    for name in (
        "reports_sales_csv",
        "reports_own_brand_csv",
        "reports_gst_csv",
        "reports_cod_credit_csv",
        "reports_stock_valuation_csv",
    ):
        response = client.get(reverse(name), PARAMS)
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")
