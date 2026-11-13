"""Tests for find_invoice_problems and check_invoice_integrity (D80)."""

from __future__ import annotations

from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.orders.invoices import find_invoice_problems
from apps.orders.models import Invoice, InvoiceLine, Order
from apps.orders.tests.factories import OrderFactory
from apps.orders.tests.invoice_helpers import sent_out_order
from apps.orders.tests.staff_helpers import NOW

pytestmark = pytest.mark.django_db


def _invoice(order: Order, number: str, *, taxable: str = "10.00") -> Invoice:
    """A hand-built, self-consistent invoice, for the rows a test breaks."""
    # The order is made to match its invoice, so a test that breaks one rule
    # does not also trip the invoice-total-equals-order rule (D82).
    order.item_total = Decimal(taxable)
    order.delivery_fee = Decimal("0.00")
    order.grand_total = Decimal(taxable)
    order.amount_due_on_delivery = Decimal(taxable)
    order.save(
        update_fields=[
            "item_total",
            "delivery_fee",
            "grand_total",
            "amount_due_on_delivery",
            "updated_at",
        ]
    )
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
        taxable_total=Decimal(taxable),
        cgst_total=Decimal("0.00"),
        sgst_total=Decimal("0.00"),
        gst_total=Decimal("0.00"),
        delivery_fee=Decimal("0.00"),
        grand_total=Decimal(taxable),
    )
    InvoiceLine.objects.create(
        invoice=invoice,
        description="Goods",
        quantity=Decimal("1.000"),
        rate=Decimal(taxable),
        gst_rate_percent=Decimal("0.00"),
        taxable_value=Decimal(taxable),
        cgst=Decimal("0.00"),
        sgst=Decimal("0.00"),
        line_total=Decimal(taxable),
    )
    return invoice


def _codes() -> list[str]:
    return [problem.code for problem in find_invoice_problems()]


def test_a_clean_database_gives_no_problems() -> None:
    sent_out_order()

    assert find_invoice_problems() == []


def test_a_missing_invoice_is_reported() -> None:
    OrderFactory(status=Order.Status.OUT_FOR_DELIVERY)

    problems = find_invoice_problems()

    assert [problem.code for problem in problems] == ["missing_invoice"]
    assert "without an invoice" in problems[0].message


def test_a_delivered_order_without_an_invoice_is_reported() -> None:
    OrderFactory(status=Order.Status.DELIVERED, delivered_at=NOW)

    assert _codes() == ["missing_invoice"]


def test_a_number_gap_is_reported() -> None:
    first = OrderFactory()
    second = OrderFactory()
    _invoice(first, "INV/26-27/000001")
    _invoice(second, "INV/26-27/000003")

    problems = find_invoice_problems()

    assert [problem.code for problem in problems] == ["number_gap"]
    # The missing number is 2; 1 and 3 exist, so the series jumps.
    assert "[2]" in problems[0].message
    assert problems[0].invoice_number == "INV/26-27"


def test_a_line_sum_mismatch_is_reported() -> None:
    order = OrderFactory()
    invoice = _invoice(order, "INV/26-27/000001", taxable="10.00")
    # The totals say 20.00 but the one line still says 10.00. The order follows
    # the invoice, so the total-mismatch rule stays quiet (D82).
    order.item_total = Decimal("20.00")
    order.grand_total = Decimal("20.00")
    order.amount_due_on_delivery = Decimal("20.00")
    order.save(
        update_fields=["item_total", "grand_total", "amount_due_on_delivery", "updated_at"],
    )
    invoice.taxable_total = Decimal("20.00")
    invoice.grand_total = Decimal("20.00")
    invoice.save(update_fields=["taxable_total", "grand_total", "updated_at"])

    problems = find_invoice_problems()

    assert [problem.code for problem in problems] == ["line_sum_mismatch"]
    assert problems[0].invoice_number == "INV/26-27/000001"


def test_the_command_reports_a_clean_database() -> None:
    sent_out_order()
    out = StringIO()

    call_command("check_invoice_integrity", stdout=out)

    assert "no problems found" in out.getvalue()


def test_the_command_fails_with_exit_code_one_on_a_problem() -> None:
    OrderFactory(status=Order.Status.OUT_FOR_DELIVERY)

    # call_command raises CommandError, which manage.py turns into exit code 1.
    with pytest.raises(CommandError):
        call_command("check_invoice_integrity", stdout=StringIO())


def test_the_command_prints_the_missing_invoice() -> None:
    order = OrderFactory(status=Order.Status.OUT_FOR_DELIVERY)
    out = StringIO()

    with pytest.raises(CommandError):
        call_command("check_invoice_integrity", stdout=out)

    printed = out.getvalue()
    assert "missing_invoice" in printed
    assert order.order_number in printed
