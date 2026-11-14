"""What the daily stock email reports: low stock and lots near expiry (D89).

Read-only: nothing in this module writes a row. The expiry figures are an
estimate, because a variant's stock is one running total and not a count per
received lot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum

from apps.catalog.models import ProductVariant
from apps.inventory.availability import get_available_quantities
from apps.inventory.models import InventoryMovement

# How many days ahead "near expiry" means.
EXPIRY_WINDOW_DAYS = 7

# Quantities are stored with three decimals, and the email prints them so.
QUANTITY_PLACES = Decimal("0.001")


@dataclass(frozen=True)
class LowStockRow:
    """One active variant at or below its own low stock threshold."""

    product: str
    variant: str
    sku: str
    unit: str
    available: Decimal
    threshold: Decimal


@dataclass(frozen=True)
class ExpiryRow:
    """One received lot near or past its expiry, with its estimated remainder."""

    product: str
    variant: str
    sku: str
    batch_number: str
    expiry_date: date
    remaining: Decimal


def variant_label(variant: ProductVariant) -> str:
    """A short pack label, so two packs of one product read differently."""
    if variant.net_quantity_label:
        return variant.net_quantity_label
    if variant.pack_size == 1:
        return f"1 {variant.unit.lower()}"
    # The ":g" format drops trailing zeros, so 1.500 prints as "1.5".
    return f"{variant.pack_size:g} {variant.unit.lower()}"


def low_stock_rows() -> list[LowStockRow]:
    """Every active variant at or below its threshold, ordered by name (D89).

    The amount is the orderable figure a customer sees (on-hand minus the
    online stock buffer and the active holds, D60). A blank threshold never
    alerts, because the filter reads only rows that have one.
    """
    variants = list(
        ProductVariant.objects.filter(
            is_active=True,
            product__is_active=True,
            low_stock_threshold__isnull=False,
        ).select_related("product")
    )
    # One grouped query for every variant, so the count of queries is fixed.
    available = get_available_quantities([variant.pk for variant in variants])
    rows: list[LowStockRow] = []
    for variant in variants:
        threshold = variant.low_stock_threshold
        # The filter above promises a threshold, but the type checker cannot
        # see that, so it is checked again here.
        if threshold is None:
            continue
        amount = available.get(variant.pk, Decimal("0.000"))
        if amount > threshold:
            continue
        rows.append(
            LowStockRow(
                product=variant.product.name_en,
                variant=variant_label(variant),
                sku=variant.sku_code,
                unit=variant.unit,
                available=amount,
                threshold=threshold,
            )
        )
    # The first key sorts by product name, the second by the pack label.
    return sorted(rows, key=lambda row: (row.product, row.variant))


def _on_hand_totals(variant_ids: list[int]) -> dict[int, Decimal]:
    """The ledger sum per variant, in one query: no buffer and no holds."""
    if not variant_ids:
        return {}
    rows = (
        InventoryMovement.objects.filter(variant_id__in=variant_ids)
        # The empty order_by() keeps the model's ordering out of GROUP BY,
        # which would otherwise split one variant into several rows.
        .order_by()
        .values("variant_id")
        .annotate(total=Sum("quantity_change"))
    )
    return {row["variant_id"]: row["total"] for row in rows}


def expiry_rows(today: date) -> tuple[list[ExpiryRow], list[ExpiryRow]]:
    """Return the expired lots and the soon-expiring ones (D89).

    Both lists are sorted by expiry date. A variant's stock is one running
    total, so the amount left in a lot is an estimate: the whole on-hand figure
    is handed to the lots newest-received first, and a lot keeps at most what
    it received. That assumes the oldest purchases are sold first. A lot with
    nothing left by that estimate is left out.

    All purchase lots of the variant take part in the hand-out, dated or not
    (48f1): a newer lot that expires later, or never, is what the shelf really
    holds, so it takes the stock before an older lot due in the window.
    """
    window_end = today + timedelta(days=EXPIRY_WINDOW_DAYS)
    # Step one: the variants that have at least one lot due in the window. A
    # variant without one can produce no row, so its lots are never loaded.
    near_variant_ids = list(
        InventoryMovement.objects.filter(
            movement_type=InventoryMovement.MovementType.PURCHASE_RECEIVED,
            expiry_date__isnull=False,
            expiry_date__lte=window_end,
            variant__is_active=True,
        )
        .values_list("variant_id", flat=True)
        .distinct()
    )
    if not near_variant_ids:
        return [], []
    # Step two: every purchase lot of those variants, with a date or without,
    # so the hand-out below sees the same lots the shelf is holding.
    lots = list(
        InventoryMovement.objects.filter(
            movement_type=InventoryMovement.MovementType.PURCHASE_RECEIVED,
            variant_id__in=near_variant_ids,
        ).select_related("variant__product")
    )
    on_hand = _on_hand_totals(near_variant_ids)
    # Newest received first: that is the order the on-hand figure is handed to.
    newest_first = sorted(lots, key=lambda lot: (lot.created_at, lot.pk), reverse=True)
    left = dict(on_hand)
    remaining: dict[int, Decimal] = {}
    for lot in newest_first:
        available = max(left.get(lot.variant_id, Decimal("0")), Decimal("0"))
        received = max(lot.quantity_change, Decimal("0"))
        kept = min(received, available)
        remaining[lot.pk] = kept
        left[lot.variant_id] = available - kept

    expired: list[ExpiryRow] = []
    expiring: list[ExpiryRow] = []
    for lot in lots:
        expiry_date = lot.expiry_date
        if expiry_date is None:
            # No date at all: the lot only shared the hand-out above.
            continue
        # A lot well past the window took part in the hand-out too, but it is
        # not news for today's email.
        if expiry_date > window_end:
            continue
        kept = remaining.get(lot.pk, Decimal("0"))
        if kept <= 0:
            continue
        row = ExpiryRow(
            product=lot.variant.product.name_en,
            variant=variant_label(lot.variant),
            sku=lot.variant.sku_code,
            batch_number=lot.batch_number,
            expiry_date=expiry_date,
            remaining=kept.quantize(QUANTITY_PLACES),
        )
        if expiry_date < today:
            expired.append(row)
        else:
            expiring.append(row)
    expired.sort(key=lambda row: (row.expiry_date, row.product, row.variant))
    expiring.sort(key=lambda row: (row.expiry_date, row.product, row.variant))
    return expired, expiring
