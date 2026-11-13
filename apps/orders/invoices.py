"""GST tax invoices: issue the one invoice of an order, cancel it, check it.

One order has at most one invoice (D13), and the number comes from the same
locked counter as an order number (``apps/core/numbering.py``), so it is
sequential and gapless (D14). Every value the printed page shows is copied onto
the invoice, so a later edit to ``ShopSettings`` or to a product can never
rewrite an invoice that was issued.

``issue_invoice`` must be called inside the transaction that sends the order
out: a refusal then changes nothing at all, and a rolled-back send-out gives
its number back to the series.

REVIEW CAREFULLY: money and tax live here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.db import transaction

from apps.accounts.models import User
from apps.catalog.services import gst_breakdown, round_money
from apps.core.models import ShopSettings
from apps.core.numbering import next_number
from apps.core.services import get_shop_settings
from apps.orders.models import Invoice, InvoiceLine, Order, OrderItem

# A blank ``ShopSettings.invoice_number_prefix`` means "not configured", so the
# series falls back to this, exactly as a blank order prefix falls back to BS.
DEFAULT_INVOICE_PREFIX = "INV"

# The GST rule caps an invoice number at 16 characters (D14, to be confirmed).
MAX_INVOICE_NUMBER_LENGTH = 16

# The shop's own state. ``ShopSettings`` has no state column yet (D80), so the
# name comes from the project brief (the shop is in Indore, Madhya Pradesh) and
# the two-digit state code is the first two characters of the GSTIN.
SHOP_STATE_NAME = "Madhya Pradesh"

# The fee line is a charge, not a product, so it prints as its own line.
DELIVERY_FEE_DESCRIPTION = "Delivery fee"

# Money is never a float (D3).
ZERO = Decimal("0.00")

# A number looks like ``INV/26-27/000001``. The three parts are read so the
# series can be checked for a gap.
NUMBER_PATTERN = re.compile(r"^(?P<prefix>.+)/(?P<year>\d{2}-\d{2})/(?P<sequence>\d{6,})$")

SEVERITY_ERROR = "ERROR"

# The two rules D82 added: the invoice total must equal its order's total, and
# the line discounts must add up to the invoice's discount.
TOTAL_MISMATCH = "total_mismatch"
DISCOUNT_MISMATCH = "discount_mismatch"


class InvoiceError(ValueError):
    """Base error for every invoice refusal."""


class ShopDetailsMissingError(InvoiceError):
    """The shop name, address or GSTIN is blank, so no tax invoice can be issued."""


class InvoiceNumberTooLongError(InvoiceError):
    """The number series would print a number longer than the GST limit."""


class InvoiceAlreadyExistsError(InvoiceError):
    """This order already has its one invoice (D13)."""


class InvoiceLineMissingError(InvoiceError):
    """The order has no fulfilled line, so there is nothing to invoice."""


@dataclass(frozen=True)
class InvoiceProblem:
    """One finding of ``find_invoice_problems``."""

    code: str
    severity: str
    invoice_number: str
    message: str


def _require_shop_details() -> ShopSettings:
    """Return the settings row, refusing a blank shop name, address or GSTIN.

    A tax invoice without the seller's name, address and GSTIN is not a valid
    document, so the send-out stops rather than printing one (D80).
    """
    settings = get_shop_settings()
    if not settings.shop_name.strip():
        raise ShopDetailsMissingError(
            "The shop name is not set, so no tax invoice can be issued.",
        )
    if not settings.address.strip():
        raise ShopDetailsMissingError(
            "The shop address is not set, so no tax invoice can be issued.",
        )
    if not settings.gstin.strip():
        raise ShopDetailsMissingError(
            "The shop GSTIN is not set, so no tax invoice can be issued.",
        )
    return settings


def _buyer_address(order: Order) -> str:
    """The frozen delivery address as a printed block, one part per line."""
    lines = [order.ship_name, order.ship_line1]
    if order.ship_line2:
        lines.append(order.ship_line2)
    if order.ship_landmark:
        lines.append(order.ship_landmark)
    lines.append(f"{order.ship_city}, {order.ship_state} {order.ship_pincode}")
    return "\n".join(lines)


def _fulfilled_items(order: Order) -> list[OrderItem]:
    """The order lines that actually went out, in the order they were placed."""
    rows = OrderItem.objects.filter(order=order).order_by("pk")
    return [item for item in rows if item.item_status == OrderItem.ItemStatus.FULFILLED]


def _allocate_discount(grosses: list[Decimal], discount: Decimal) -> list[Decimal]:
    """Share a discount across lines in proportion to each line's gross value.

    Every line before the last line with a gross above zero gets
    ``round_money(discount * gross / sum_of_gross)``; that last line takes what
    is left, so the shares add up to ``discount`` exactly (D82). A line with a
    zero gross gets zero, and no discount gives every line zero.
    """
    # ``sum`` with a start value keeps the whole addition in ``Decimal``.
    total = sum(grosses, ZERO)
    if discount <= ZERO or total <= ZERO:
        return [ZERO for _gross in grosses]
    # The last line that carries value absorbs the rounding remainder.
    last = max(index for index, gross in enumerate(grosses) if gross > ZERO)
    shares: list[Decimal] = []
    allocated = ZERO
    for index, gross in enumerate(grosses):
        if index == last:
            share = round_money(discount) - allocated
        elif gross <= ZERO:
            share = ZERO
        else:
            share = round_money(discount * gross / total)
        shares.append(share)
        allocated += share
    return shares


def _product_line(item: OrderItem, discount: Decimal) -> dict[str, Any]:
    """One product line, with its GST worked out from the GST-inclusive price.

    The line's share of the item discount comes off its gross value first, so
    the GST is charged on the discounted value; ``rate`` stays the full unit
    price (D82). ``gst_breakdown`` is the one place that decides how tax is
    rounded (D3).
    """
    gross = round_money(item.unit_price * item.quantity_fulfilled)
    breakdown = gst_breakdown(round_money(gross - discount), item.gst_rate_percent)
    return {
        "description": item.product_name,
        "hsn_code": item.hsn_code,
        "unit": item.unit,
        "quantity": item.quantity_fulfilled,
        "rate": item.unit_price,
        "discount_amount": discount,
        "gst_rate_percent": item.gst_rate_percent,
        "taxable_value": breakdown.taxable_value,
        "cgst": breakdown.cgst,
        "sgst": breakdown.sgst,
        "line_total": breakdown.price_inclusive,
        "is_fee_line": False,
    }


def _fee_line(delivery_fee: Decimal, gst_rate_percent: Decimal) -> dict[str, Any]:
    """The one delivery-fee line, taxed at the shop's delivery-fee rate.

    ``delivery_fee`` is the amount actually charged, so it is already net of a
    delivery discount (D82).
    """
    breakdown = gst_breakdown(delivery_fee, gst_rate_percent)
    return {
        "description": DELIVERY_FEE_DESCRIPTION,
        "hsn_code": "",
        "unit": "",
        "quantity": Decimal("1.000"),
        "rate": breakdown.price_inclusive,
        "discount_amount": ZERO,
        "gst_rate_percent": gst_rate_percent,
        "taxable_value": breakdown.taxable_value,
        "cgst": breakdown.cgst,
        "sgst": breakdown.sgst,
        "line_total": breakdown.price_inclusive,
        "is_fee_line": True,
    }


def _sum(rows: list[dict[str, Any]], key: str) -> Decimal:
    """Add one money column of every line, starting from a ``Decimal`` zero.

    The ``for`` inside the parentheses is a generator expression: it adds the
    values one at a time without building a list first.
    """
    return sum((row[key] for row in rows), ZERO)


def issue_invoice(order: Order, *, actor: User | None, now: datetime) -> Invoice:
    """Issue the one tax invoice of this order and return it (D13, D80).

    The snapshot comes from the fulfilled quantities and the delivery fee, so
    it is exactly what left the shop. ``actor`` is accepted because every
    writer in this project takes the person behind it; no ``AuditLog`` row is
    written, because the invoice row and the order status log are the history.
    """
    settings = _require_shop_details()
    if Invoice.objects.filter(order=order).exists():
        raise InvoiceAlreadyExistsError("This order already has an invoice.")

    items = _fulfilled_items(order)
    if not items:
        raise InvoiceLineMissingError("This order has no fulfilled line to invoice.")
    grosses = [round_money(item.unit_price * item.quantity_fulfilled) for item in items]
    shares = _allocate_discount(grosses, order.discount_total)
    rows = [_product_line(item, share) for item, share in zip(items, shares, strict=True)]
    # The fee line is the fee actually charged, and there is none when a
    # free-delivery coupon took it all (D82).
    charged_fee = round_money(order.delivery_fee - order.delivery_discount)
    if charged_fee > ZERO:
        rows.append(_fee_line(charged_fee, settings.delivery_fee_gst_percent))

    # The counter row is locked by ``next_number`` itself, inside this
    # transaction, so a refusal below returns the number to the series.
    prefix = settings.invoice_number_prefix.strip() or DEFAULT_INVOICE_PREFIX
    number = next_number(prefix, when=now)
    if len(number) > MAX_INVOICE_NUMBER_LENGTH:
        raise InvoiceNumberTooLongError(
            f"The invoice number {number!r} is longer than {MAX_INVOICE_NUMBER_LENGTH} characters.",
        )

    cgst_total = _sum(rows, "cgst")
    sgst_total = _sum(rows, "sgst")
    invoice = Invoice.objects.create(
        order=order,
        invoice_number=number,
        issued_at=now,
        seller_name=settings.shop_name.strip(),
        seller_address=settings.address.strip(),
        seller_gstin=settings.gstin.strip().upper(),
        seller_state=SHOP_STATE_NAME,
        seller_state_code=settings.gstin.strip()[:2].upper(),
        buyer_name=order.ship_name,
        buyer_phone=order.ship_phone,
        buyer_address=_buyer_address(order),
        place_of_supply=order.ship_state,
        taxable_total=_sum(rows, "taxable_value"),
        cgst_total=cgst_total,
        sgst_total=sgst_total,
        gst_total=cgst_total + sgst_total,
        delivery_fee=charged_fee,
        discount_total=order.discount_total,
        coupon_code=order.coupon_code,
        grand_total=_sum(rows, "line_total"),
    )
    for row in rows:
        InvoiceLine.objects.create(invoice=invoice, **row)
    return invoice


def mark_invoice_cancelled(order: Order, reason: str, now: datetime) -> Invoice | None:
    """Mark the order's invoice cancelled when the order is cancelled (D80).

    An order that never got an invoice returns ``None`` and nothing is written.
    The number is kept and the row is never deleted, so the series stays
    gapless; marking the same invoice twice changes nothing.
    """
    # A ``select_for_update()`` needs an open transaction. The send-out and the
    # staff cancel already hold one; this keeps a direct call from failing.
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().filter(order=order).first()
        if invoice is None:
            return None
        if invoice.cancelled_at is not None:
            return invoice
        invoice.cancelled_at = now
        invoice.cancel_reason = (reason or "").strip()[:200]
        invoice.save(update_fields=["cancelled_at", "cancel_reason", "updated_at"])
        return invoice


def find_invoice_problems() -> list[InvoiceProblem]:
    """Return every broken invoice rule found, in a stable order (D80, D82).

    Five rules are checked: every line sum equals its invoice total, the line
    discounts add up to the invoice's discount, the invoice total equals its
    order's total, every order that went out has an invoice, and every number
    series has no gap. Nothing here writes or locks anything.
    """
    problems: list[InvoiceProblem] = []

    # 1. The sum of the line totals must be the invoice's grand total.
    for invoice in Invoice.objects.select_related("order").prefetch_related("lines"):
        # ``.[...].all()`` reads the prefetched rows, so this is one query.
        lines = list(invoice.lines.all())
        line_sum = sum((line.line_total for line in lines), ZERO)
        if line_sum != invoice.grand_total:
            problems.append(
                InvoiceProblem(
                    code="line_sum_mismatch",
                    severity=SEVERITY_ERROR,
                    invoice_number=invoice.invoice_number,
                    message=(
                        f"The lines add up to {line_sum} but the invoice total is "
                        f"{invoice.grand_total}."
                    ),
                )
            )
        # 1b. The line discounts must add up to the invoice's discount.
        discount_sum = sum((line.discount_amount for line in lines), ZERO)
        if discount_sum != invoice.discount_total:
            problems.append(
                InvoiceProblem(
                    code=DISCOUNT_MISMATCH,
                    severity=SEVERITY_ERROR,
                    invoice_number=invoice.invoice_number,
                    message=(
                        f"The line discounts add up to {discount_sum} but the invoice "
                        f"discount is {invoice.discount_total}."
                    ),
                )
            )
        # 1c. The invoice total must be the total of the order it came from.
        if invoice.grand_total != invoice.order.grand_total:
            problems.append(
                InvoiceProblem(
                    code=TOTAL_MISMATCH,
                    severity=SEVERITY_ERROR,
                    invoice_number=invoice.invoice_number,
                    message=(
                        f"The invoice total is {invoice.grand_total} but order "
                        f"{invoice.order.order_number} totals {invoice.order.grand_total}."
                    ),
                )
            )

    # 2. An order that left the shop must have its invoice.
    sent_out = Order.objects.filter(
        status__in=(Order.Status.OUT_FOR_DELIVERY, Order.Status.DELIVERED),
        invoice__isnull=True,
    )
    for order in sent_out:
        problems.append(
            InvoiceProblem(
                code="missing_invoice",
                severity=SEVERITY_ERROR,
                invoice_number="",
                message=f"Order {order.order_number} went out without an invoice.",
            )
        )

    # 3. Every number series must count 1, 2, 3 ... with nothing missing.
    series: dict[tuple[str, str], list[int]] = {}
    for number in Invoice.objects.values_list("invoice_number", flat=True):
        match = NUMBER_PATTERN.match(number)
        if match is None:
            continue
        key = (match.group("prefix"), match.group("year"))
        # ``setdefault`` creates the empty list on first use and returns it.
        series.setdefault(key, []).append(int(match.group("sequence")))
    for (prefix, year), numbers in sorted(series.items()):
        missing = sorted(set(range(1, max(numbers) + 1)) - set(numbers))
        if missing:
            problems.append(
                InvoiceProblem(
                    code="number_gap",
                    severity=SEVERITY_ERROR,
                    invoice_number=f"{prefix}/{year}",
                    message=f"The series {prefix}/{year} is missing {missing}.",
                )
            )

    problems.sort(key=lambda problem: (problem.code, problem.invoice_number))
    return problems
