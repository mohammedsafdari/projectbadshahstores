"""Tests for the InventoryMovement ledger model."""

import datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError, models
from django.db.models import Sum

from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.models import InventoryMovement

from .factories import InventoryMovementFactory, SupplierFactory

# Every test below touches the database.
pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "movement_type,quantity_change",
    [
        (InventoryMovement.MovementType.PURCHASE_RECEIVED, Decimal("5")),
        (InventoryMovement.MovementType.RETURN, Decimal("1")),
        (InventoryMovement.MovementType.IN_STORE_SALE, Decimal("-1")),
        (InventoryMovement.MovementType.SALE, Decimal("-2")),
        (InventoryMovement.MovementType.DAMAGE, Decimal("-1")),
        (
            InventoryMovement.MovementType.ADJUSTMENT,
            Decimal("3"),
        ),
        (
            InventoryMovement.MovementType.ADJUSTMENT,
            Decimal("-3"),
        ),
    ],
)
def test_valid_movement_pairs_save(
    movement_type: str,
    quantity_change: Decimal,
) -> None:
    movement = InventoryMovementFactory(
        movement_type=movement_type,
        quantity_change=quantity_change,
        reason="Reason for the adjustment",
    )
    movement.full_clean()
    movement.refresh_from_db()
    assert movement.quantity_change == quantity_change


@pytest.mark.parametrize(
    "movement_type,quantity_change",
    [
        (InventoryMovement.MovementType.PURCHASE_RECEIVED, Decimal("-1")),
        (InventoryMovement.MovementType.RETURN, Decimal("-1")),
        (InventoryMovement.MovementType.IN_STORE_SALE, Decimal("1")),
        (InventoryMovement.MovementType.SALE, Decimal("1")),
        (InventoryMovement.MovementType.DAMAGE, Decimal("1")),
        (InventoryMovement.MovementType.PURCHASE_RECEIVED, Decimal("0")),
        (
            InventoryMovement.MovementType.ADJUSTMENT,
            Decimal("0"),
        ),
    ],
)
def test_invalid_movement_pairs_raise_integrityerror(
    movement_type: str,
    quantity_change: Decimal,
) -> None:
    # build() makes objects in memory only, including sub-objects, and Django
    # will not save a row that points at an unsaved parent.
    variant = ProductVariantFactory()
    movement = InventoryMovementFactory.build(
        variant=variant,
        movement_type=movement_type,
        quantity_change=quantity_change,
        reason="Reason for the adjustment",
    )
    with pytest.raises(IntegrityError):
        movement.save()


def test_adjustment_with_empty_reason_is_rejected() -> None:
    # build() makes objects in memory only, including sub-objects, and Django
    # will not save a row that points at an unsaved parent.
    variant = ProductVariantFactory()
    movement = InventoryMovementFactory.build(
        variant=variant,
        movement_type=InventoryMovement.MovementType.ADJUSTMENT,
        quantity_change=Decimal("1"),
        reason="",
    )
    with pytest.raises(IntegrityError):
        movement.save()


def test_adjustment_with_reason_saves() -> None:
    movement = InventoryMovementFactory(
        movement_type=InventoryMovement.MovementType.ADJUSTMENT,
        quantity_change=Decimal("1"),
        reason="Reason for the adjustment",
    )
    movement.refresh_from_db()
    assert movement.quantity_change == Decimal("1")


def test_batch_number_on_non_purchase_is_rejected() -> None:
    # build() makes objects in memory only, including sub-objects, and Django
    # will not save a row that points at an unsaved parent.
    variant = ProductVariantFactory()
    movement = InventoryMovementFactory.build(
        variant=variant,
        movement_type=InventoryMovement.MovementType.SALE,
        quantity_change=Decimal("-1"),
        batch_number="Batch 1",
    )
    with pytest.raises(IntegrityError):
        movement.save()


def test_expiry_date_on_non_purchase_is_rejected() -> None:
    # build() makes objects in memory only, including sub-objects, and Django
    # will not save a row that points at an unsaved parent.
    variant = ProductVariantFactory()
    movement = InventoryMovementFactory.build(
        variant=variant,
        movement_type=InventoryMovement.MovementType.SALE,
        quantity_change=Decimal("-1"),
        expiry_date=datetime.date(2026, 12, 31),
    )
    with pytest.raises(IntegrityError):
        movement.save()


def test_purchase_with_supplier_batch_and_expiry_saves() -> None:
    supplier = SupplierFactory()
    movement = InventoryMovementFactory(
        movement_type=InventoryMovement.MovementType.PURCHASE_RECEIVED,
        quantity_change=Decimal("10"),
        supplier=supplier,
        batch_number="Batch 1",
        expiry_date=datetime.date(2026, 12, 31),
    )
    movement.refresh_from_db()
    assert movement.supplier_id == supplier.id
    assert movement.batch_number == "Batch 1"
    assert movement.expiry_date == datetime.date(2026, 12, 31)


def test_loose_variant_movement_roundtrips() -> None:
    movement = InventoryMovementFactory(
        movement_type=InventoryMovement.MovementType.PURCHASE_RECEIVED,
        quantity_change=Decimal("0.350"),
    )
    movement.refresh_from_db()
    assert movement.quantity_change == Decimal("0.350")


def test_deleting_supplier_with_movements_raises_protectederror() -> None:
    supplier = SupplierFactory()
    # The factory call creates the movement in the database, so the supplier
    # is protected; the result is not needed.
    InventoryMovementFactory(supplier=supplier)
    with pytest.raises(models.deletion.ProtectedError):
        supplier.delete()


def test_deleting_variant_with_movements_raises_protectederror() -> None:
    movement = InventoryMovementFactory()
    with pytest.raises(models.deletion.ProtectedError):
        movement.variant.delete()


def test_on_hand_is_the_sum() -> None:
    movement = InventoryMovementFactory(
        movement_type=InventoryMovement.MovementType.PURCHASE_RECEIVED,
        quantity_change=Decimal("25.000"),
    )
    movement2 = InventoryMovementFactory.build(
        variant=movement.variant,
        movement_type=InventoryMovement.MovementType.IN_STORE_SALE,
        quantity_change=Decimal("-0.750"),
    )
    movement2.save()
    movement3 = InventoryMovementFactory.build(
        variant=movement.variant,
        movement_type=InventoryMovement.MovementType.DAMAGE,
        quantity_change=Decimal("-2.250"),
    )
    movement3.save()
    # Sum returns None when a variant has no movements at all.
    total = movement.variant.movements.aggregate(
        total=Sum("quantity_change"),
    )["total"]
    assert total == Decimal("22.000")
