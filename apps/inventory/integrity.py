"""Read-only integrity check for the stock ledger (ADR D43, point 8).

Nothing here writes or locks anything. The database already refuses zero
quantities, wrongly signed rows, adjustments with an empty reason and batch or
expiry data on non-purchases. This module looks for the rules that only the
services enforce, in case a row got into the ledger around them (raw SQL, or a
variant switched from loose to packet later).
"""

from dataclasses import dataclass

from django.db.models import Sum
from django.db.models.functions import Floor

from apps.inventory.models import InventoryMovement

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
