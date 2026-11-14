"""Sales by day, product, brand and category, from the frozen invoice figures (D85)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from apps.accounts.models import User
from apps.catalog.tests.factories import BrandFactory, CategoryFactory
from apps.orders.invoices import mark_invoice_cancelled
from apps.orders.models import Invoice, Order
from apps.orders.staff_services import pack_and_send_out
from apps.orders.tests.factories import OrderFactory, OrderItemFactory
from apps.orders.tests.staff_helpers import NOW, hold_for, staff
from apps.reports.queries import (
    SALES_BY_BRAND,
    SALES_BY_CATEGORY,
    SALES_BY_DAY,
    SALES_BY_PRODUCT,
    SALES_KEYS,
    DateRange,
    ReportsPermissionError,
    sales_report,
)
from apps.reports.tests.helpers import FIRST_DAY, IST, LAST_DAY, make_variant, sold
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

NOVEMBER = DateRange(FIRST_DAY, LAST_DAY)


def _goods_totals() -> tuple[Decimal, Decimal]:
    """Taxable value and line total of every non-fee line of the live invoices."""
    lines = Invoice.objects.filter(cancelled_at__isnull=True).values_list(
        "lines__is_fee_line", "lines__taxable_value", "lines__line_total"
    )
    taxable = Decimal("0.00")
    total = Decimal("0.00")
    for is_fee_line, line_taxable, line_total in lines:
        if is_fee_line or line_taxable is None:
            continue
        taxable += line_taxable
        total += line_total
    return taxable, total


def test_sales_by_day_has_one_row_per_indian_day() -> None:
    variant = make_variant()
    sold(lines=[(variant, "2")], when=datetime(2026, 11, 1, 10, 0, tzinfo=IST))
    sold(lines=[(variant, "1")], when=datetime(2026, 11, 2, 10, 0, tzinfo=IST))

    report = sales_report(staff(), NOVEMBER, SALES_BY_DAY)

    assert [row.label for row in report.rows] == ["2026-11-01", "2026-11-02"]


def test_sales_by_product_uses_the_frozen_product_name() -> None:
    atta = make_variant(name="Atta")
    rice = make_variant(name="Rice")
    sold(lines=[(atta, "2")])
    sold(lines=[(rice, "1")])

    report = sales_report(staff(), NOVEMBER, SALES_BY_PRODUCT)

    assert {row.label for row in report.rows} == {"Atta", "Rice"}


def test_sales_by_brand_groups_the_lines_of_one_brand() -> None:
    brand = BrandFactory(name="Nestle")
    other = BrandFactory(name="Amul")
    sold(lines=[(make_variant(name="KitKat", brand=brand), "2")])
    sold(lines=[(make_variant(name="Munch", brand=brand), "1")])
    sold(lines=[(make_variant(name="Milk", brand=other), "1")])

    report = sales_report(staff(), NOVEMBER, SALES_BY_BRAND)

    labels = [row.label for row in report.rows]
    assert labels == ["Nestle", "Amul"]
    assert report.rows[0].totals.total > report.rows[1].totals.total


def test_sales_by_category_groups_the_lines_of_one_category() -> None:
    category = CategoryFactory(name="Snacks")
    other = CategoryFactory(name="Dairy")
    sold(lines=[(make_variant(name="Chips", category=category), "2")])
    sold(lines=[(make_variant(name="Curd", category=other), "1")])

    report = sales_report(staff(), NOVEMBER, SALES_BY_CATEGORY)

    assert {row.label for row in report.rows} == {"Snacks", "Dairy"}


def test_the_four_sections_add_up_to_the_same_sales_total() -> None:
    sold(lines=[(make_variant(name="Atta"), "2")])
    sold(lines=[(make_variant(name="Rice"), "3")])
    _taxable, expected = _goods_totals()

    totals = {key: sales_report(staff(), NOVEMBER, key).totals.total for key in SALES_KEYS}

    assert set(totals.values()) == {expected}
    assert expected > Decimal("0.00")


def test_the_sales_total_leaves_out_the_delivery_fee_line() -> None:
    _order, invoice = sold(lines=[(make_variant(), "2")])
    fee = invoice.lines.get(is_fee_line=True)

    report = sales_report(staff(), NOVEMBER, SALES_BY_DAY)

    assert report.totals.total == invoice.grand_total - fee.line_total
    assert report.totals.total < invoice.grand_total


def test_a_loose_quantity_keeps_three_decimals() -> None:
    loose = make_variant(loose=True, price="80.00", stock="10.000")
    sold(lines=[(loose, "2.5")])

    report = sales_report(staff(), NOVEMBER, SALES_BY_PRODUCT)

    assert report.rows[0].totals.units == Decimal("2.500")
    assert report.totals.units == Decimal("2.500")


def test_a_cancelled_invoice_is_not_in_any_total() -> None:
    variant = make_variant()
    order, _invoice = sold(lines=[(variant, "2")], when=datetime(2026, 11, 3, 9, 0, tzinfo=IST))
    mark_invoice_cancelled(order, "Customer changed their mind", NOW)

    report = sales_report(staff(), NOVEMBER, SALES_BY_DAY)

    assert report.totals.total == Decimal("0.00")
    assert report.totals.units == Decimal("0.000")
    assert report.rows == []


def test_a_cancelled_invoice_is_shown_on_its_own_line() -> None:
    variant = make_variant()
    order, invoice = sold(lines=[(variant, "2")], when=datetime(2026, 11, 3, 9, 0, tzinfo=IST))
    mark_invoice_cancelled(order, "Out of stock", NOW)

    report = sales_report(staff(), NOVEMBER, SALES_BY_PRODUCT)

    assert report.cancelled_count == 1
    assert report.cancelled_total == invoice.grand_total


def _discounted_order(variant) -> Invoice:
    """A packing order with a 20.00 coupon discount, sent out through the service."""
    customer = UserFactory()
    group = uuid4()
    hold_for(variant, customer, group=group, quantity="2")
    order = OrderFactory(
        user=customer,
        status=Order.Status.PACKING,
        checkout_group=group,
        item_total=Decimal("190.00"),
        discount_total=Decimal("20.00"),
        delivery_fee=Decimal("30.00"),
        grand_total=Decimal("200.00"),
    )
    OrderItemFactory(order=order, variant=variant, quantity_ordered=Decimal("2"))
    pack_and_send_out(order.pk, actor=staff(), now=NOW)
    return Invoice.objects.get(order=order)


def test_a_coupon_discount_is_read_from_the_invoice() -> None:
    invoice = _discounted_order(make_variant(price="95.00"))
    line = invoice.lines.get(is_fee_line=False)

    report = sales_report(staff(), NOVEMBER, SALES_BY_PRODUCT)

    assert invoice.discount_total == Decimal("20.00")
    assert line.discount_amount == Decimal("20.00")
    assert report.totals.taxable == line.taxable_value
    assert report.totals.total == line.line_total


def test_a_khata_order_counts_as_a_sale() -> None:
    sold(
        lines=[(make_variant(), "2")],
        payment_method=Order.PaymentMethod.CREDIT,
    )
    _taxable, expected = _goods_totals()

    report = sales_report(staff(), NOVEMBER, SALES_BY_PRODUCT)

    assert report.totals.total == expected


def test_an_invoice_just_after_indian_midnight_belongs_to_that_day() -> None:
    variant = make_variant()
    sold(lines=[(variant, "1")], when=datetime(2026, 11, 1, 0, 30, tzinfo=IST))

    report = sales_report(
        staff(),
        DateRange(FIRST_DAY, FIRST_DAY),
        SALES_BY_DAY,
    )

    assert [row.label for row in report.rows] == ["2026-11-01"]


def test_an_invoice_before_indian_midnight_belongs_to_the_day_before() -> None:
    variant = make_variant()
    sold(lines=[(variant, "1")], when=datetime(2026, 10, 31, 23, 30, tzinfo=IST))

    report = sales_report(staff(), DateRange(FIRST_DAY, FIRST_DAY), SALES_BY_DAY)

    assert report.rows == []
    assert report.totals.total == Decimal("0.00")


def test_an_unknown_section_is_refused() -> None:
    with pytest.raises(ValueError):
        sales_report(staff(), NOVEMBER, "week")


def test_a_user_without_the_capability_cannot_read_a_sales_report() -> None:
    with pytest.raises(ReportsPermissionError):
        sales_report(staff(User.ROLE_PACKER), NOVEMBER, SALES_BY_DAY)
