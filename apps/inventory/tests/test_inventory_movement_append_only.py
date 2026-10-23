"""Tests that InventoryMovement is append-only."""

from decimal import Decimal

import pytest

from apps.inventory.models import AppendOnlyError, InventoryMovement

from .factories import InventoryMovementFactory

# Every test below touches the database.
pytestmark = pytest.mark.django_db


def test_movement_can_be_created_with_factory() -> None:
    movement = InventoryMovementFactory()
    assert movement.id is not None


def test_movement_can_be_created_with_objects_create() -> None:
    variant = InventoryMovementFactory().variant
    movement = InventoryMovement.objects.create(
        variant=variant,
        movement_type=InventoryMovement.MovementType.PURCHASE_RECEIVED,
        quantity_change=Decimal("5.000"),
    )
    assert movement.id is not None
    assert movement.quantity_change == Decimal("5.000")


def test_movement_can_be_bulk_created() -> None:
    variant = InventoryMovementFactory().variant
    movements = [
        InventoryMovement(
            variant=variant,
            movement_type=InventoryMovement.MovementType.PURCHASE_RECEIVED,
            quantity_change=Decimal("1.000"),
        ),
        InventoryMovement(
            variant=variant,
            movement_type=InventoryMovement.MovementType.PURCHASE_RECEIVED,
            quantity_change=Decimal("2.000"),
        ),
    ]
    created = InventoryMovement.objects.bulk_create(movements)
    assert len(created) == 2
    assert created[0].quantity_change == Decimal("1.000")


def test_saving_existing_movement_after_change_raises() -> None:
    movement = InventoryMovementFactory(
        quantity_change=Decimal("10.000"),
    )
    movement.quantity_change = Decimal("20.000")
    with pytest.raises(AppendOnlyError):
        movement.save()
    movement.refresh_from_db()
    assert movement.quantity_change == Decimal("10.000")


def test_saving_existing_movement_without_change_raises() -> None:
    movement = InventoryMovementFactory(
        quantity_change=Decimal("10.000"),
    )
    with pytest.raises(AppendOnlyError):
        movement.save()


def test_delete_raises_and_row_still_exists() -> None:
    movement = InventoryMovementFactory()
    with pytest.raises(AppendOnlyError):
        movement.delete()
    movement.refresh_from_db()
    assert movement.id is not None


def test_queryset_update_raises_and_value_unchanged() -> None:
    movement = InventoryMovementFactory(
        quantity_change=Decimal("10.000"),
    )
    with pytest.raises(AppendOnlyError):
        InventoryMovement.objects.filter(pk=movement.pk).update(
            quantity_change=Decimal("20.000"),
        )
    movement.refresh_from_db()
    assert movement.quantity_change == Decimal("10.000")


def test_queryset_delete_raises_and_row_still_exists() -> None:
    movement = InventoryMovementFactory()
    with pytest.raises(AppendOnlyError):
        InventoryMovement.objects.all().delete()
    movement.refresh_from_db()
    assert movement.id is not None


def test_bulk_update_raises() -> None:
    movement = InventoryMovementFactory(
        quantity_change=Decimal("10.000"),
    )
    with pytest.raises(AppendOnlyError):
        InventoryMovement.objects.bulk_update(
            [movement],
            ["quantity_change"],
        )
