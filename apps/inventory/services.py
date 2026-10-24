"""Inventory app business logic: stock movements, on-hand, receipt and adjustments.

Everything that writes ``InventoryMovement`` rows goes through one private
function, ``_record_movement``, so the guards here stay single-source (ADR D43).
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Sum

from apps.accounts.models import User
from apps.catalog.models import ProductVariant
from apps.inventory.models import InventoryMovement, Supplier

logger = logging.getLogger(__name__)

QUANTITY_PLACES = Decimal("0.001")
MAX_ABS_QUANTITY = Decimal("1000000000")
REASON_MAX_LENGTH = 200
BATCH_MAX_LENGTH = 60


class InventoryError(ValueError):
    """Base error for inventory service problems."""


class InvalidQuantityError(InventoryError):
    """The quantity passed to the service did not pass validation."""


class InsufficientStockError(InventoryError):
    """An action would take on-hand below the allowed level."""


def _clean_quantity(quantity: object, *, variant: ProductVariant) -> Decimal:
    """Validate and normalise a quantity for the given variant (ADR D43, point 3).

    Accepts Decimal or int. Rejects bool, float, str, NaN, Infinity, more than
    3 decimal places, an absolute value at or over MAX_ABS_QUANTITY, and any
    non-whole number on a packet variant. Returns the value quantised to 3
    places. The sign is checked by the caller, not here.
    """
    # bool is a subclass of int, so check it first.
    if isinstance(quantity, bool):
        raise InvalidQuantityError("Quantity must be a Decimal or int, not a boolean.")
    if isinstance(quantity, int):
        value: Decimal = Decimal(quantity)
    elif isinstance(quantity, Decimal):
        if not quantity.is_finite():
            raise InvalidQuantityError("Quantity must be a finite number.")
        value = quantity
    else:
        raise InvalidQuantityError(
            f"Quantity must be a Decimal or int, not {type(quantity).__name__!r}."
        )

    if abs(value) >= MAX_ABS_QUANTITY:
        raise InvalidQuantityError(
            f"Quantity must be less than {MAX_ABS_QUANTITY} in absolute value."
        )

    # Quantise with the same exponent the database uses, then compare. That
    # accepts Decimal("2.5000") because it is equal to Decimal("2.500").
    quantised = value.quantize(QUANTITY_PLACES)
    if value != quantised:
        raise InvalidQuantityError("Quantity must have at most 3 decimal places.")

    if not variant.is_sold_loose and value != value.to_integral_value():
        raise InvalidQuantityError("Packet variants accept whole numbers only.")

    return quantised


def _clean_text(value: str, *, name: str, max_length: int, required: bool) -> str:
    """Strip whitespace and validate a text field (ADR D43, point 3).

    ``name`` is only used in the error message, so callers can say which field
    failed. ``required`` means an empty value after stripping is an error.
    """
    cleaned = value.strip()
    if required and not cleaned:
        raise InventoryError(f"{name} must not be empty.")
    if len(cleaned) > max_length:
        raise InventoryError(f"{name} must be at most {max_length} characters.")
    return cleaned


def _lock_variant(variant: ProductVariant) -> ProductVariant:
    """Lock and reload a variant inside a transaction (ADR D43, point 2).

    It must only be called inside ``transaction.atomic()``, because
    ``select_for_update`` only makes sense while a transaction is open.
    """
    return ProductVariant.objects.select_for_update().get(pk=variant.pk)


def _sum_on_hand(variant: ProductVariant) -> Decimal:
    """Return current on-hand as the sum of the variant's movements (ADR D42)."""
    total: Any = InventoryMovement.objects.filter(
        variant_id=variant.pk,
    ).aggregate(total=Sum("quantity_change"))["total"]
    if total is None:
        return Decimal("0")
    return total


def get_on_hand(variant: ProductVariant) -> Decimal:
    """Current on-hand for a variant, with no lock (ADR D43, point 4)."""
    return _sum_on_hand(variant)


def _record_movement(
    *,
    variant: ProductVariant,
    movement_type: str,
    quantity_change: Decimal,
    reason: str,
    supplier: Supplier | None,
    batch_number: str,
    expiry_date: date | None,
    created_by: User | None,
) -> InventoryMovement:
    """The ONLY place that creates ``InventoryMovement`` rows (ADR D43, point 1).

    The keyword-only signature makes it awkward to call by accident from anywhere
    else, but the real guard is code review and this comment.
    """
    return InventoryMovement.objects.create(
        variant=variant,
        movement_type=movement_type,
        quantity_change=quantity_change,
        reason=reason,
        supplier=supplier,
        batch_number=batch_number,
        expiry_date=expiry_date,
        created_by=created_by,
    )


def receive_stock(
    *,
    variant: ProductVariant,
    quantity: object,
    supplier: Supplier | None = None,
    batch_number: str = "",
    expiry_date: date | None = None,
    reason: str = "",
    created_by: User | None = None,
) -> InventoryMovement:
    """Receive stock into a variant (ADR D43, point 4).

    Runs inside one transaction, locks the variant, validates everything, then
    writes one ``PURCHASE_RECEIVED`` movement. A rejected call writes nothing.
    """
    with transaction.atomic():
        locked = _lock_variant(variant)

        quantity_change = _clean_quantity(quantity, variant=locked)
        if quantity_change <= 0:
            raise InventoryError("A purchase must increase stock.")

        if supplier is not None and not supplier.is_active:
            raise InventoryError("An archived supplier cannot receive stock.")

        if expiry_date is not None:
            if isinstance(expiry_date, datetime):
                raise InventoryError("Expiry date must be a date, not a datetime.")
            if not isinstance(expiry_date, date):
                raise InventoryError(
                    f"Expiry date must be a date, not {type(expiry_date).__name__!r}."
                )

        batch_number = _clean_text(
            batch_number, name="Batch number", max_length=BATCH_MAX_LENGTH, required=False
        )
        reason = _clean_text(reason, name="Reason", max_length=REASON_MAX_LENGTH, required=False)

        return _record_movement(
            variant=locked,
            movement_type=InventoryMovement.MovementType.PURCHASE_RECEIVED,
            quantity_change=quantity_change,
            reason=reason,
            supplier=supplier,
            batch_number=batch_number,
            expiry_date=expiry_date,
            created_by=created_by,
        )
