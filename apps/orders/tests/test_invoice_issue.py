"""Tests for issuing the one GST tax invoice of an order (D13, D14, D80).

Every test drives the real send-out service, so the invoice is always built
from what really left the shop.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.catalog.services import round_money
from apps.inventory.models import InventoryMovement, StockReservation
from apps.orders.invoices import (
    InvoiceAlreadyExistsError,
    InvoiceNumberTooLongError,
    ShopDetailsMissingError,
    issue_invoice,
)
from apps.orders.models import Invoice, Order
from apps.orders.services import InvalidTransitionError
from apps.orders.staff_services import pack_and_send_out
from apps.orders.tests.invoice_helpers import (
    SHOP_GSTIN,
    SHOP_NAME,
    fill_shop_details,
    gst_variant,
    packing_order,
    sent_out_order,
)
from apps.orders.tests.staff_helpers import NOW, staff

pytestmark = pytest.mark.django_db


def test_invoice_number_has_the_inv_prefix_and_the_financial_year() -> None:
    _order, invoice = sent_out_order()

    assert invoice.invoice_number == "INV/26-27/000001"


def test_invoice_number_is_at_most_sixteen_characters() -> None:
    _order, invoice = sent_out_order()

    assert len(invoice.invoice_number) <= 16


def test_a_number_longer_than_sixteen_characters_is_refused() -> None:
    # "INVOICE-LONG/26-27/000001" is 26 characters, past the GST limit.
    fill_shop_details(invoice_number_prefix="INVOICE-LONG")
    order, _variants = packing_order()

    with pytest.raises(InvoiceNumberTooLongError):
        pack_and_send_out(order.pk, actor=staff(), now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.PACKING
    assert not Invoice.objects.exists()


def test_two_orders_get_consecutive_numbers() -> None:
    _first_order, first = sent_out_order()
    _second_order, second = sent_out_order()

    assert first.invoice_number == "INV/26-27/000001"
    assert second.invoice_number == "INV/26-27/000002"


def test_a_rolled_back_send_out_leaves_no_gap() -> None:
    order, _variants = packing_order()

    # The invoice is issued inside the send-out transaction, so the status move
    # failing rolls the number back with everything else.
    with (
        patch(
            "apps.orders.staff_services.transition_order",
            side_effect=InvalidTransitionError("boom"),
        ),
        pytest.raises(InvalidTransitionError),
    ):
        pack_and_send_out(order.pk, actor=staff(), now=NOW)

    assert not Invoice.objects.exists()
    _next_order, invoice = sent_out_order()
    assert invoice.invoice_number == "INV/26-27/000001"


def test_one_invoice_per_order() -> None:
    order, _invoice = sent_out_order()

    with pytest.raises(InvoiceAlreadyExistsError):
        issue_invoice(order, actor=staff(), now=NOW)

    assert Invoice.objects.filter(order=order).count() == 1


def test_the_snapshot_survives_a_shop_settings_edit() -> None:
    _order, invoice = sent_out_order()
    assert invoice.seller_name == SHOP_NAME
    assert invoice.seller_gstin == SHOP_GSTIN

    fill_shop_details(
        shop_name="Another Shop",
        address="Somewhere else",
        gstin="07BBBBB1111B1Z5",
    )

    invoice.refresh_from_db()
    assert invoice.seller_name == SHOP_NAME
    assert invoice.seller_gstin == SHOP_GSTIN


def test_the_snapshot_survives_a_product_edit() -> None:
    row = gst_variant(price="95.00", gst="5.00")
    _order, invoice = sent_out_order(lines=[(row, "2")])
    line = invoice.lines.get(is_fee_line=False)
    old_description = line.description

    row.selling_price = Decimal("150.00")
    row.gst_rate_percent = Decimal("12.00")
    row.save(update_fields=["selling_price", "gst_rate_percent", "updated_at"])
    row.product.name_en = "Renamed product"
    row.product.save(update_fields=["name_en", "updated_at"])

    line.refresh_from_db()
    assert line.rate == Decimal("95.00")
    assert line.gst_rate_percent == Decimal("5.00")
    assert line.description == old_description


def test_the_delivery_fee_is_one_fee_line() -> None:
    _order, invoice = sent_out_order(delivery_fee="30.00")

    fee_lines = invoice.lines.filter(is_fee_line=True)
    assert fee_lines.count() == 1
    fee_line = fee_lines.get()
    assert fee_line.description == "Delivery fee"
    assert fee_line.line_total == Decimal("30.00")
    assert invoice.delivery_fee == Decimal("30.00")


def test_a_free_delivery_order_has_no_fee_line() -> None:
    _order, invoice = sent_out_order(delivery_fee="0.00")

    assert not invoice.lines.filter(is_fee_line=True).exists()
    assert invoice.delivery_fee == Decimal("0.00")
    assert invoice.grand_total == invoice.lines.get().line_total


def test_the_cgst_and_sgst_split_per_line() -> None:
    row = gst_variant(price="100.00", gst="5.00")
    _order, invoice = sent_out_order(lines=[(row, "2")], delivery_fee="0.00")

    line = invoice.lines.get(is_fee_line=False)
    # 200.00 already includes 5% GST: 9.52 of tax, split into 4.76 and 4.76.
    assert line.gst_rate_percent == Decimal("5.00")
    assert line.taxable_value == Decimal("190.48")
    assert line.cgst == Decimal("4.76")
    assert line.sgst == Decimal("4.76")
    assert line.line_total == Decimal("200.00")


def test_cgst_plus_sgst_is_the_gst_total() -> None:
    _order, invoice = sent_out_order()

    assert invoice.cgst_total + invoice.sgst_total == invoice.gst_total
    assert invoice.taxable_total + invoice.gst_total == invoice.grand_total


def test_the_line_totals_add_up_to_the_order_grand_total() -> None:
    first = gst_variant(price="95.00", gst="5.00")
    second = gst_variant(price="41.50", gst="12.00")
    order, invoice = sent_out_order(
        lines=[(first, "2"), (second, "3")],
        delivery_fee="30.00",
    )

    line_sum = sum((line.line_total for line in invoice.lines.all()), Decimal("0.00"))
    assert line_sum == order.grand_total
    assert invoice.grand_total == order.grand_total


def test_a_loose_item_with_three_decimals_keeps_its_fraction() -> None:
    row = gst_variant(price="12.34", gst="5.00", loose=True, stock="5.000")
    _order, invoice = sent_out_order(lines=[(row, "1.234")], delivery_fee="0.00")

    line = invoice.lines.get(is_fee_line=False)
    assert line.quantity == Decimal("1.234")
    # 12.34 a kilogram for 1.234 kg is 15.22756, rounded half-up to 15.23.
    assert line.line_total == Decimal("15.23")
    assert line.line_total == round_money(Decimal("12.34") * Decimal("1.234"))


def test_a_loose_item_line_breaks_down_its_gst() -> None:
    row = gst_variant(price="12.34", gst="5.00", loose=True)
    _order, invoice = sent_out_order(lines=[(row, "1.234")], delivery_fee="0.00")

    line = invoice.lines.get(is_fee_line=False)
    # 15.23 includes 0.73 of GST, split into 0.37 and 0.36.
    assert line.taxable_value == Decimal("14.50")
    assert line.cgst == Decimal("0.37")
    assert line.sgst == Decimal("0.36")
    assert line.cgst + line.sgst == Decimal("0.73")


def test_a_blank_shop_name_refuses_the_send_out() -> None:
    fill_shop_details(shop_name="")
    order, _variants = packing_order()
    hold = StockReservation.objects.get(checkout_group=order.checkout_group)

    with pytest.raises(ShopDetailsMissingError):
        pack_and_send_out(order.pk, actor=staff(), now=NOW)

    order.refresh_from_db()
    hold.refresh_from_db()
    assert order.status == Order.Status.PACKING
    assert hold.status == StockReservation.Status.ACTIVE
    assert not InventoryMovement.objects.filter(
        movement_type=InventoryMovement.MovementType.SALE,
    ).exists()
    assert not Invoice.objects.exists()


def test_a_blank_address_refuses_the_send_out() -> None:
    fill_shop_details(address="   ")
    order, _variants = packing_order()
    hold = StockReservation.objects.get(checkout_group=order.checkout_group)

    with pytest.raises(ShopDetailsMissingError) as error:
        pack_and_send_out(order.pk, actor=staff(), now=NOW)

    assert "address" in str(error.value)
    order.refresh_from_db()
    hold.refresh_from_db()
    assert order.status == Order.Status.PACKING
    assert hold.status == StockReservation.Status.ACTIVE
    assert not Invoice.objects.exists()


def test_a_blank_gstin_refuses_the_send_out() -> None:
    fill_shop_details(gstin="")
    order, _variants = packing_order()
    hold = StockReservation.objects.get(checkout_group=order.checkout_group)

    with pytest.raises(ShopDetailsMissingError) as error:
        pack_and_send_out(order.pk, actor=staff(), now=NOW)

    assert "GSTIN" in str(error.value)
    order.refresh_from_db()
    hold.refresh_from_db()
    assert order.status == Order.Status.PACKING
    assert hold.status == StockReservation.Status.ACTIVE
    assert not InventoryMovement.objects.filter(
        movement_type=InventoryMovement.MovementType.SALE,
    ).exists()
    assert not Invoice.objects.exists()


def test_a_blank_prefix_uses_inv() -> None:
    fill_shop_details(invoice_number_prefix="")

    _order, invoice = sent_out_order()

    assert invoice.invoice_number.startswith("INV/")


def test_the_invoice_is_issued_at_the_given_time() -> None:
    _order, invoice = sent_out_order()

    assert invoice.issued_at == NOW


def test_the_place_of_supply_is_the_buyer_state() -> None:
    order, invoice = sent_out_order()

    assert order.ship_state == "Madhya Pradesh"
    assert invoice.place_of_supply == order.ship_state
    assert invoice.buyer_name == order.ship_name
    assert invoice.buyer_phone == order.ship_phone


def test_the_seller_state_code_comes_from_the_gstin() -> None:
    _order, invoice = sent_out_order()

    assert invoice.seller_state_code == SHOP_GSTIN[:2]
    assert invoice.seller_state == "Madhya Pradesh"
