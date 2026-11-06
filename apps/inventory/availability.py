"""How much of a variant a customer may order right now (D55, D60, D63).

On-hand comes from the ledger (D42). The online stock buffer in
``ShopSettings`` is taken off it, so the shop can keep a few packets for the
counter. Active reservations are taken off too (D63): a fulfilment hold counts
until it is consumed or released, and a checkout hold counts only while its
``expires_at`` is strictly after now. The result never goes below zero.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from apps.catalog.models import ProductVariant
from apps.core.services import get_shop_settings
from apps.inventory.models import InventoryMovement, StockReservation

QUANTITY_PLACES = Decimal("0.001")


def get_held_quantities(
    variant_ids: Iterable[int], now: datetime | None = None
) -> dict[int, Decimal]:
    """Return the active held amount for every id, in three decimal places.

    One grouped query sums the holds for all ids, so the number of queries
    does not grow with the size of the list. Duplicate ids are fine. A variant
    with no holds comes back as ``Decimal("0.000")``. A checkout hold counts
    only when ``expires_at`` is strictly after ``now``; a hold expiring exactly
    at ``now`` no longer counts.
    """
    # A set drops duplicate ids; the caller does not need them twice.
    ids = set(variant_ids)
    if not ids:
        return {}
    if now is None:
        now = timezone.now()

    rows = (
        StockReservation.objects.filter(
            variant_id__in=ids,
            status=StockReservation.Status.ACTIVE,
        )
        .filter(
            Q(kind=StockReservation.Kind.HELD_FOR_FULFILMENT)
            | Q(
                kind=StockReservation.Kind.CHECKOUT_PENDING_PAYMENT,
                expires_at__gt=now,
            )
        )
        # The empty order_by() clears the model's default ordering, which
        # would otherwise join the GROUP BY and split one variant into rows.
        .order_by()
        .values("variant_id")
        .annotate(total=Sum("quantity"))
        .values_list("variant_id", "total")
    )
    totals = dict(rows)

    held: dict[int, Decimal] = {}
    for variant_id in ids:
        amount = totals.get(variant_id, Decimal("0"))
        held[variant_id] = amount.quantize(QUANTITY_PLACES)
    return held


def get_available_quantities(
    variant_ids: Iterable[int], now: datetime | None = None
) -> dict[int, Decimal]:
    """Return the orderable amount for every id, in three decimal places.

    One grouped query sums the ledger and one sums the active holds, so the
    number of queries does not grow with the size of the list. Duplicate ids
    are fine. A variant with no movements, or one whose stock minus the buffer
    and the holds is at or below zero, comes back as ``Decimal("0.000")``.
    """
    # A set drops duplicate ids; the caller does not need them twice.
    ids = set(variant_ids)
    if not ids:
        return {}

    rows = (
        InventoryMovement.objects.filter(variant_id__in=ids)
        # See get_held_quantities: keep the default ordering out of GROUP BY.
        .order_by()
        .values("variant_id")
        .annotate(total=Sum("quantity_change"))
        .values_list("variant_id", "total")
    )
    totals = dict(rows)
    held = get_held_quantities(ids, now=now)

    buffer = Decimal(get_shop_settings().online_stock_buffer)

    available: dict[int, Decimal] = {}
    for variant_id in ids:
        on_hand = totals.get(variant_id, Decimal("0"))
        amount = on_hand - buffer - held[variant_id]
        if amount < 0:
            amount = Decimal("0")
        available[variant_id] = amount.quantize(QUANTITY_PLACES)
    return available


def get_available_quantity(variant: ProductVariant, now: datetime | None = None) -> Decimal:
    """The orderable amount for one variant (a thin wrapper)."""
    return get_available_quantities([variant.pk], now=now)[variant.pk]
