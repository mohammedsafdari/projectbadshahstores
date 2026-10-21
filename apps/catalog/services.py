"""Pricing helpers for catalog variants.

Everything here works on Decimal rupees (INR), never float, and does no
database access. That makes it easy to test and easy to call from views,
the admin, invoices or a future API.
"""

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from .models import ProductVariant

PAISE = Decimal("0.01")
HUNDRED = Decimal("100")


def round_money(amount: Decimal) -> Decimal:
    """Round to whole paise (two decimals), with halves going up.

    REVIEW CAREFULLY: this is the single place that decides how money is
    rounded. Confirm the rule with your CA before invoices are issued;
    changing it here changes it everywhere.
    """
    return amount.quantize(PAISE, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class GstBreakdown:
    """The GST inside a GST-inclusive price.

    For sales inside Madhya Pradesh the tax is split into cgst + sgst.
    For inter-state sales (later phase) the whole gst_amount is IGST.
    """

    price_inclusive: Decimal
    taxable_value: Decimal
    gst_amount: Decimal
    cgst: Decimal
    sgst: Decimal


def gst_breakdown(
    price_inclusive: Decimal,
    gst_rate_percent: Decimal,
) -> GstBreakdown:
    """Split a GST-inclusive price into taxable value and GST.

    REVIEW CAREFULLY: money. Verify the rounding rule with your CA.
    """
    if price_inclusive < 0:
        raise ValueError("price must not be negative")
    if not Decimal("0") <= gst_rate_percent <= HUNDRED:
        raise ValueError("GST rate must be between 0 and 100")
    price = round_money(price_inclusive)
    # The price already includes GST, so the tax inside it is
    # price * rate / (100 + rate).
    gst_amount = round_money(price * gst_rate_percent / (HUNDRED + gst_rate_percent))
    cgst = round_money(gst_amount / 2)
    # SGST is the remainder, so cgst + sgst always equals gst_amount exactly.
    sgst = gst_amount - cgst
    return GstBreakdown(
        price_inclusive=price,
        taxable_value=price - gst_amount,
        gst_amount=gst_amount,
        cgst=cgst,
        sgst=sgst,
    )


def _check_prices(mrp: Decimal, selling_price: Decimal) -> None:
    if mrp <= 0:
        raise ValueError("MRP must be positive")
    if selling_price < 0 or selling_price > mrp:
        raise ValueError("selling price must be between 0 and the MRP")


def savings_amount(mrp: Decimal, selling_price: Decimal) -> Decimal:
    """Rupees saved compared with the MRP."""
    _check_prices(mrp, selling_price)
    return mrp - selling_price


def savings_percent(mrp: Decimal, selling_price: Decimal) -> Decimal:
    """Whole-number percent saved, rounded DOWN so we never overstate it."""
    _check_prices(mrp, selling_price)
    percent = (mrp - selling_price) / mrp * HUNDRED
    return percent.quantize(Decimal("1"), rounding=ROUND_DOWN)


@dataclass(frozen=True)
class UnitPrice:
    """A price for a standard quantity, e.g. 10.00 per 100 g."""

    amount: Decimal
    label: str


# unit value -> (how many units the price is quoted for, label to show)
_UNIT_PRICING: dict[str, tuple[Decimal, str]] = {
    ProductVariant.Unit.KG.value: (Decimal("1"), "per kg"),
    ProductVariant.Unit.G.value: (Decimal("100"), "per 100 g"),
    ProductVariant.Unit.ML.value: (Decimal("100"), "per 100 ml"),
    ProductVariant.Unit.L.value: (Decimal("1"), "per litre"),
    ProductVariant.Unit.PIECE.value: (Decimal("1"), "per piece"),
    ProductVariant.Unit.PACKET.value: (Decimal("1"), "per packet"),
}


def unit_price(selling_price: Decimal, pack_size: Decimal, unit: str) -> UnitPrice:
    """Price per standard quantity, so shoppers can compare pack sizes."""
    if pack_size <= 0:
        raise ValueError("pack size must be positive")
    if unit not in _UNIT_PRICING:
        raise ValueError(f"unknown unit: {unit}")
    quantity, label = _UNIT_PRICING[unit]
    return UnitPrice(
        amount=round_money(selling_price * quantity / pack_size),
        label=label,
    )
