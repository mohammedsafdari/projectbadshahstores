"""Tests for the discount on the GST invoice (D82)."""

from __future__ import annotations

import re
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse

from apps.catalog.models import ProductVariant
from apps.orders.invoice_pdf import _line_rows, build_invoice_pdf
from apps.orders.invoices import (
    DISCOUNT_MISMATCH,
    TOTAL_MISMATCH,
    _allocate_discount,
    find_invoice_problems,
)
from apps.orders.models import Invoice, InvoiceLine, Order
from apps.orders.staff_services import pack_and_send_out
from apps.orders.tests.factories import OrderFactory
from apps.orders.tests.invoice_helpers import gst_variant, packing_order, sent_out_order
from apps.orders.tests.staff_helpers import NOW, staff
from apps.promotions.models import Coupon
from apps.promotions.tests.helpers import make_coupon

pytestmark = pytest.mark.django_db

ZERO = Decimal("0.00")


def _discounted_invoice(
    *,
    lines: list[tuple[ProductVariant, str]] | None = None,
    discount: str = "20.00",
    delivery_discount: str = "0.00",
    delivery_fee: str = "30.00",
    coupon_code: str = "DIWALI10",
) -> tuple[Order, Invoice]:
    """A sent-out order whose money carries the given coupon discounts."""
    order, _variants = packing_order(lines=lines, delivery_fee=delivery_fee)
    fee = Decimal(delivery_fee)
    item_discount = Decimal(discount)
    fee_discount = Decimal(delivery_discount)
    # The order factory builds the undiscounted total, so the coupon money is
    # written in one go, with a total that satisfies the order's equation.
    total = order.item_total - item_discount + fee - fee_discount
    Order.objects.filter(pk=order.pk).update(
        discount_total=item_discount,
        delivery_discount=fee_discount,
        coupon_code=coupon_code,
        grand_total=total,
        # A cash order owes its whole total at the door, so this moves too.
        amount_due_on_delivery=total,
    )
    order.refresh_from_db()
    pack_and_send_out(order.pk, actor=staff(), now=NOW)
    order.refresh_from_db()
    return order, Invoice.objects.get(order=order)


def _hand_invoice(order: Order, *, number: str, total: str, discount: str = "0.00") -> Invoice:
    """A hand-built invoice for the order, to break one rule on purpose."""
    invoice = Invoice.objects.create(
        order=order,
        invoice_number=number,
        issued_at=NOW,
        seller_name="Badshah Store",
        seller_address="Indore",
        seller_gstin="23AAAAA0000A1Z5",
        seller_state="Madhya Pradesh",
        seller_state_code="23",
        buyer_name="Asha Sharma",
        buyer_phone="+919876543210",
        buyer_address="Indore",
        place_of_supply="Madhya Pradesh",
        taxable_total=Decimal(total),
        cgst_total=ZERO,
        sgst_total=ZERO,
        gst_total=ZERO,
        delivery_fee=ZERO,
        discount_total=Decimal(discount),
        grand_total=Decimal(total),
    )
    InvoiceLine.objects.create(
        invoice=invoice,
        description="Goods",
        quantity=Decimal("1.000"),
        rate=Decimal(total),
        gst_rate_percent=Decimal("0.00"),
        taxable_value=Decimal(total),
        cgst=ZERO,
        sgst=ZERO,
        line_total=Decimal(total),
    )
    return invoice


# --- the pure sharing helper -------------------------------------------------


def test_the_discount_is_shared_in_proportion() -> None:
    shares = _allocate_discount([Decimal("100.00"), Decimal("300.00")], Decimal("40.00"))

    assert shares == [Decimal("10.00"), Decimal("30.00")]


def test_the_odd_paisa_goes_to_the_earliest_line() -> None:
    shares = _allocate_discount(
        [Decimal("100.00"), Decimal("100.00"), Decimal("100.00")],
        Decimal("10.00"),
    )

    # 10.00 does not divide by three, so one line takes the odd paisa; the
    # fractions are equal, so the tie goes to the earliest line (D82).
    assert shares == [Decimal("3.34"), Decimal("3.33"), Decimal("3.33")]
    assert sum(shares, ZERO) == Decimal("10.00")


def test_an_odd_paisa_is_kept_whole() -> None:
    shares = _allocate_discount(
        [Decimal("1.00"), Decimal("1.00"), Decimal("1.00")],
        Decimal("1.00"),
    )

    assert shares == [Decimal("0.34"), Decimal("0.33"), Decimal("0.33")]
    assert sum(shares, ZERO) == Decimal("1.00")


def test_a_zero_gross_line_gets_no_discount() -> None:
    shares = _allocate_discount([Decimal("0.00"), Decimal("200.00")], Decimal("20.00"))

    assert shares == [ZERO, Decimal("20.00")]


def test_a_zero_gross_last_line_gets_no_discount() -> None:
    shares = _allocate_discount([Decimal("200.00"), Decimal("0.00")], Decimal("20.00"))

    assert shares == [Decimal("20.00"), ZERO]


def test_a_discount_equal_to_the_sum_takes_everything() -> None:
    shares = _allocate_discount([Decimal("100.00"), Decimal("300.00")], Decimal("400.00"))

    assert shares == [Decimal("100.00"), Decimal("300.00")]


def test_a_single_line_takes_the_whole_discount() -> None:
    assert _allocate_discount([Decimal("200.00")], Decimal("20.00")) == [Decimal("20.00")]


def test_no_discount_gives_every_line_zero() -> None:
    assert _allocate_discount([Decimal("100.00"), Decimal("300.00")], ZERO) == [ZERO, ZERO]


def test_no_lines_give_no_shares() -> None:
    assert _allocate_discount([], Decimal("10.00")) == []


def test_the_shares_always_add_up() -> None:
    grosses = [Decimal("33.33"), Decimal("66.67"), Decimal("12.50")]

    shares = _allocate_discount(grosses, Decimal("7.77"))

    assert sum(shares, ZERO) == Decimal("7.77")
    assert all(share >= ZERO for share in shares)


# --- GST on the discounted value ---------------------------------------------


def test_the_gst_is_worked_out_on_the_discounted_value() -> None:
    row = gst_variant(price="100.00", gst="5.00")
    _order, invoice = _discounted_invoice(lines=[(row, "2")])

    line = invoice.lines.get(is_fee_line=False)

    # 200.00 gross less a 20.00 discount is 180.00, so the tax is inside 180.00.
    assert line.taxable_value == Decimal("171.43")
    assert line.cgst == Decimal("4.29")
    assert line.sgst == Decimal("4.28")
    assert line.line_total == Decimal("180.00")


def test_the_twelve_percent_line_uses_the_discounted_value_too() -> None:
    row = gst_variant(price="100.00", gst="12.00")
    _order, invoice = _discounted_invoice(lines=[(row, "2")])

    line = invoice.lines.get(is_fee_line=False)

    assert line.taxable_value == Decimal("160.71")
    assert line.cgst == Decimal("9.65")
    assert line.sgst == Decimal("9.64")
    assert line.line_total == Decimal("180.00")


def test_a_zero_rate_line_keeps_its_discounted_total() -> None:
    row = gst_variant(price="100.00", gst="0.00")
    _order, invoice = _discounted_invoice(lines=[(row, "2")])

    line = invoice.lines.get(is_fee_line=False)

    assert line.taxable_value == Decimal("180.00")
    assert line.cgst == ZERO
    assert line.line_total == Decimal("180.00")


def test_the_line_carries_its_discount_and_the_full_rate() -> None:
    row = gst_variant(price="100.00", gst="5.00")
    _order, invoice = _discounted_invoice(lines=[(row, "2")])

    line = invoice.lines.get(is_fee_line=False)

    assert line.discount_amount == Decimal("20.00")
    assert line.rate == row.selling_price


def test_the_lines_add_up_to_the_order_total() -> None:
    row = gst_variant(price="100.00", gst="5.00")
    order, invoice = _discounted_invoice(lines=[(row, "2")])

    assert invoice.grand_total == order.grand_total == Decimal("210.00")
    assert sum((line.line_total for line in invoice.lines.all()), ZERO) == order.grand_total


def test_a_free_delivery_order_has_no_fee_line() -> None:
    row = gst_variant(price="100.00", gst="5.00")
    order, invoice = _discounted_invoice(lines=[(row, "2")], delivery_fee="0.00")

    assert order.delivery_fee == ZERO
    assert list(invoice.lines.filter(is_fee_line=True)) == []
    assert invoice.delivery_fee == ZERO


def test_a_charged_fee_is_the_fee_line() -> None:
    row = gst_variant(price="100.00", gst="5.00")
    order, invoice = _discounted_invoice(
        lines=[(row, "2")],
        delivery_discount="10.00",
    )

    fee_line = invoice.lines.get(is_fee_line=True)

    # 30.00 fee less a 10.00 delivery discount is what the customer paid.
    assert fee_line.line_total == Decimal("20.00")
    assert invoice.delivery_fee == Decimal("20.00")
    assert invoice.grand_total == order.grand_total == Decimal("200.00")


def test_a_zero_total_order_still_gets_an_invoice() -> None:
    row = gst_variant(price="100.00", gst="5.00")
    order, invoice = _discounted_invoice(
        lines=[(row, "2")],
        discount="200.00",
        delivery_fee="0.00",
    )

    assert order.grand_total == ZERO
    assert invoice.grand_total == ZERO
    assert invoice.lines.get(is_fee_line=False).line_total == ZERO


def test_the_invoice_keeps_the_discount_and_the_code() -> None:
    row = gst_variant(price="100.00", gst="5.00")
    order, invoice = _discounted_invoice(lines=[(row, "2")])

    assert invoice.discount_total == Decimal("20.00")
    assert invoice.coupon_code == "DIWALI10"
    assert invoice.coupon_code == order.coupon_code


def test_the_snapshot_survives_a_later_edit() -> None:
    row = gst_variant(price="100.00", gst="5.00")
    order, invoice = _discounted_invoice(lines=[(row, "2")])
    coupon = make_coupon()

    # The order's code and the coupon behind it are changed after the issue.
    Order.objects.filter(pk=order.pk).update(coupon_code="OTHER10")
    Coupon.objects.filter(pk=coupon.pk).update(description="Changed")
    invoice.refresh_from_db()

    assert invoice.coupon_code == "DIWALI10"
    assert invoice.discount_total == Decimal("20.00")
    assert invoice.lines.get(is_fee_line=False).discount_amount == Decimal("20.00")


def test_an_invoice_without_a_discount_is_unchanged() -> None:
    row = gst_variant(price="100.00", gst="5.00")
    order, invoice = _discounted_invoice(
        lines=[(row, "2")],
        discount="0.00",
        coupon_code="",
    )

    assert invoice.discount_total == ZERO
    assert invoice.coupon_code == ""
    assert invoice.delivery_fee == Decimal("30.00")
    assert invoice.grand_total == order.grand_total == Decimal("230.00")
    assert invoice.lines.get(is_fee_line=True).line_total == Decimal("30.00")


# --- the integrity rules -----------------------------------------------------


def test_an_invoice_total_that_differs_from_its_order_is_reported() -> None:
    order = OrderFactory()
    _hand_invoice(order, number="INV/26-27/000001", total="10.00")

    problems = find_invoice_problems()

    assert [problem.code for problem in problems] == [TOTAL_MISMATCH]
    assert "230.00" in problems[0].message


def test_line_discounts_that_do_not_add_up_are_reported() -> None:
    order = OrderFactory()
    _hand_invoice(order, number="INV/26-27/000001", total="230.00", discount="5.00")

    problems = find_invoice_problems()

    assert [problem.code for problem in problems] == [DISCOUNT_MISMATCH]


def test_a_discounted_invoice_has_no_problems() -> None:
    row = gst_variant(price="100.00", gst="5.00")
    _discounted_invoice(lines=[(row, "2")])

    assert find_invoice_problems() == []


def test_the_integrity_command_fails_on_a_total_mismatch() -> None:
    order = OrderFactory()
    _hand_invoice(order, number="INV/26-27/000001", total="10.00")

    with pytest.raises(CommandError):
        call_command("check_invoice_integrity", stdout=StringIO())


# --- the printed pages -------------------------------------------------------


def test_the_pdf_shows_the_discount_column_and_line() -> None:
    row = gst_variant(price="100.00", gst="5.00")
    _order, invoice = _discounted_invoice(lines=[(row, "2")])

    content = build_invoice_pdf(invoice)

    # reportlab writes a paragraph in pieces, so the words are checked alone.
    assert b"Disc." in content
    assert b"Discount" in content
    assert b"DIWALI10" in content


def test_a_plain_pdf_shows_no_discount() -> None:
    _order, invoice = sent_out_order()

    content = build_invoice_pdf(invoice)

    # The "Disc." column heading is always printed; the totals line is not.
    assert b"Discount" not in content


def test_the_pdf_prints_each_lines_discount_in_its_own_column() -> None:
    row = gst_variant(price="100.00", gst="5.00")
    _order, invoice = _discounted_invoice(lines=[(row, "2")])

    rows = _line_rows(list(invoice.lines.all()))

    # The headings come first; the product line follows, its Disc. cell at 4.
    assert rows[1][4] == "Rs. 20.00"


def test_the_pdf_leaves_the_discount_cell_empty_without_a_discount() -> None:
    _order, invoice = sent_out_order()

    rows = _line_rows(list(invoice.lines.all()))

    assert rows[1][4] == ""


def _row_cells(html: str, marker: str) -> list[str]:
    """The cells of the first table row that holds this text, tags removed."""
    for row in re.findall(r"<tr>.*?</tr>", html, re.DOTALL):
        if marker in row:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            return [re.sub(r"<[^>]+>", "", cell).strip() for cell in cells]
    raise AssertionError(f"No row with {marker}.")


def test_the_staff_page_prints_each_lines_discount_in_its_own_column() -> None:
    row = gst_variant(price="100.00", gst="5.00")
    order, invoice = _discounted_invoice(lines=[(row, "2")])
    line = invoice.lines.get(is_fee_line=False)
    client = Client()
    client.force_login(staff())

    html = client.get(reverse("staff_invoice", args=[order.order_number])).content.decode()
    cells = _row_cells(html, line.description)

    # Description, HSN, quantity, rate, Disc., taxable, CGST, SGST, total.
    assert cells[4] == "Rs. 20.00"
    assert cells[5] == "Rs. 171.43"


def test_an_invoice_total_above_its_order_is_reported() -> None:
    order = OrderFactory()
    _hand_invoice(order, number="INV/26-27/000001", total="1000.00")

    problems = find_invoice_problems()

    assert [problem.code for problem in problems] == [TOTAL_MISMATCH]


def test_the_staff_page_shows_the_discount() -> None:
    row = gst_variant(price="100.00", gst="5.00")
    order, _invoice = _discounted_invoice(lines=[(row, "2")])
    client = Client()
    client.force_login(staff())

    html = client.get(reverse("staff_invoice", args=[order.order_number])).content.decode()

    assert "Disc." in html
    assert "DIWALI10" in html
    assert "Discount" in html
