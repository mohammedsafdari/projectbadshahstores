"""Read-only integrity check for the stock ledger (ADR D43, point 8).

Nothing here writes or locks anything. The database already refuses zero
quantities, wrongly signed rows, adjustments with an empty reason and batch or
expiry data on non-purchases. This module looks for the rules that only the
services enforce, in case a row got into the ledger around them (raw SQL, or a
variant switched from loose to packet later).
"""

from dataclasses import dataclass
from decimal import Decimal

from django.db import models
from django.db.models import Sum
from django.db.models.functions import Floor
from django.utils import timezone

from apps.inventory.models import InventoryMovement, StockReservation

SEVERITY_ERROR = "ERROR"
SEVERITY_WARNING = "WARNING"


# A dataclass is a class for which Python writes __init__, __repr__ and == from
# the field list. frozen=True makes an instance read-only after it is created.
@dataclass(frozen=True)
class IntegrityProblem:
    """One finding of find_integrity_problems."""

    code: str
    severity: str
    variant_sku: str
    movement_id: int | None
    message: str


def find_integrity_problems() -> list[IntegrityProblem]:
    """Return every ledger problem found, ERRORs first.

    ERROR means a rule was broken. WARNING means the data is legal but worth a
    look. Every check selects its rows in the database; nothing is locked.
    """
    problems: list[IntegrityProblem] = []

    # 1. Fractional quantity on a packet variant (whole numbers only).
    fractional = (
        InventoryMovement.objects.filter(variant__is_sold_loose=False)
        .exclude(quantity_change=Floor("quantity_change"))
        .select_related("variant")
    )
    for movement in fractional:
        problems.append(
            IntegrityProblem(
                code="fractional_on_packet",
                severity=SEVERITY_ERROR,
                variant_sku=movement.variant.sku_code,
                movement_id=movement.pk,
                message=(
                    f"Movement {movement.pk} has quantity {movement.quantity_change} "
                    "on a packet variant (whole numbers only)."
                ),
            )
        )

    # 2. Adjustment whose reason is empty or only whitespace. The database only
    # forbids the exactly-empty string.
    blank_reasons = InventoryMovement.objects.filter(
        movement_type=InventoryMovement.MovementType.ADJUSTMENT,
        reason__regex=r"^\s*$",
    ).select_related("variant")
    for movement in blank_reasons:
        problems.append(
            IntegrityProblem(
                code="blank_adjustment_reason",
                severity=SEVERITY_ERROR,
                variant_sku=movement.variant.sku_code,
                movement_id=movement.pk,
                message=(
                    f"Adjustment {movement.pk} ({movement.quantity_change}) has a blank reason."
                ),
            )
        )

    # 3. A supplier on a movement that is not a purchase.
    wrong_supplier = (
        InventoryMovement.objects.filter(supplier__isnull=False)
        .exclude(movement_type=InventoryMovement.MovementType.PURCHASE_RECEIVED)
        .select_related("variant")
    )
    for movement in wrong_supplier:
        problems.append(
            IntegrityProblem(
                code="supplier_on_non_purchase",
                severity=SEVERITY_ERROR,
                variant_sku=movement.variant.sku_code,
                movement_id=movement.pk,
                message=(
                    f"Movement {movement.pk} ({movement.movement_type}) has a supplier "
                    "but is not a purchase."
                ),
            )
        )

    # 4. Negative on-hand is allowed after a reasoned adjustment, so it is only
    # a warning. The empty order_by() clears the model's default ordering so it
    # cannot change how the rows are grouped.
    negative = (
        InventoryMovement.objects.order_by()
        .values("variant_id", "variant__sku_code")
        .annotate(total=Sum("quantity_change"))
        .filter(total__lt=0)
    )
    for row in negative:
        problems.append(
            IntegrityProblem(
                code="negative_on_hand",
                severity=SEVERITY_WARNING,
                variant_sku=row["variant__sku_code"],
                movement_id=None,
                message=f"On-hand for variant {row['variant_id']} is {row['total']}, below zero.",
            )
        )

    # 5. Active holds above on-hand. The database allows this because physical
    # stock is the truth: damage, adjustment and count are never blocked by a
    # hold (D63). A checkout hold counts only while it has not expired.
    now = timezone.now()
    held_rows = (
        StockReservation.objects.filter(status=StockReservation.Status.ACTIVE)
        .filter(
            models.Q(kind=StockReservation.Kind.HELD_FOR_FULFILMENT)
            | models.Q(
                kind=StockReservation.Kind.CHECKOUT_PENDING_PAYMENT,
                expires_at__gt=now,
            )
        )
        .order_by()
        .values("variant_id", "variant__sku_code")
        .annotate(total=Sum("quantity"))
    )
    on_hand_rows = (
        InventoryMovement.objects.order_by()
        .values("variant_id")
        .annotate(total=Sum("quantity_change"))
    )
    on_hand = {row["variant_id"]: row["total"] for row in on_hand_rows}
    for row in held_rows:
        stock = on_hand.get(row["variant_id"], Decimal("0"))
        if row["total"] > stock:
            problems.append(
                IntegrityProblem(
                    code="over_reserved",
                    severity=SEVERITY_ERROR,
                    variant_sku=row["variant__sku_code"],
                    movement_id=None,
                    message=(
                        f"Variant {row['variant_id']} has {row['total']} held "
                        f"but only {stock} on hand."
                    ),
                )
            )

    severity_rank = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1}
    problems.sort(
        key=lambda problem: (
            severity_rank[problem.severity],
            problem.code,
            problem.variant_sku,
            problem.movement_id or 0,
        )
    )
    return problems
