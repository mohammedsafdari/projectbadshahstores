"""How much of a variant a customer may order right now (D55, D60).

On-hand comes from the ledger (D42). The online stock buffer in
``ShopSettings`` is taken off it, so the shop can keep a few packets for the
counter. The result never goes below zero.

There is no reservation yet. Slice 34 subtracts active reservations inside
``get_available_quantities``; callers will not change.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from django.db.models import Sum

from apps.catalog.models import ProductVariant
from apps.core.services import get_shop_settings
from apps.inventory.models import InventoryMovement

QUANTITY_PLACES = Decimal("0.001")


def get_available_quantities(variant_ids: Iterable[int]) -> dict[int, Decimal]:
    """Return the orderable amount for every id, in three decimal places.

    One grouped query sums the ledger for all ids, so the number of queries
    does not grow with the size of the list. Duplicate ids are fine. A variant
    with no movements, or one whose stock is at or below the buffer, comes back
    as ``Decimal("0.000")``.
    """
    # A set drops duplicate ids; the caller does not need them twice.
    ids = set(variant_ids)
    if not ids:
        return {}

    rows = (
        InventoryMovement.objects.filter(variant_id__in=ids)
        .values("variant_id")
        .annotate(total=Sum("quantity_change"))
        .values_list("variant_id", "total")
    )
    totals = dict(rows)

    buffer = Decimal(get_shop_settings().online_stock_buffer)

    available: dict[int, Decimal] = {}
    for variant_id in ids:
        on_hand = totals.get(variant_id, Decimal("0"))
        amount = on_hand - buffer
        if amount < 0:
            amount = Decimal("0")
        available[variant_id] = amount.quantize(QUANTITY_PLACES)
    return available


def get_available_quantity(variant: ProductVariant) -> Decimal:
    """The orderable amount for one variant (a thin wrapper)."""
    return get_available_quantities([variant.pk])[variant.pk]
