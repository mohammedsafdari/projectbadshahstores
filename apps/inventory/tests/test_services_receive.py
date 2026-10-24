"""Tests for inventory services: on-hand and receive_stock (Slice 16b)."""

import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.models import InventoryMovement
from apps.inventory.services import (
    BATCH_MAX_LENGTH,
    REASON_MAX_LENGTH,
    InvalidQuantityError,
    InventoryError,
    _clean_quantity,
    _clean_text,
    get_on_hand,
    receive_stock,
)
from apps.inventory.tests.factories import (
    InventoryMovementFactory,
    SupplierFactory,
)

User = get_user_model()

# Every test below touches the database.
pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers for creating a fresh variant and movement counts
# ---------------------------------------------------------------------------


def _purchase_count_for(variant: ProductVariant) -> int:
    return InventoryMovement.objects.filter(
        variant=variant, movement_type=InventoryMovement.MovementType.PURCHASE_RECEIVED
    ).count()


# ---------------------------------------------------------------------------
# get_on_hand
# ---------------------------------------------------------------------------


def test_get_on_hand_is_zero_with_no_movements() -> None:
    variant = ProductVariantFactory()
    assert get_on_hand(variant) == Decimal("0")


def test_get_on_hand_is_the_sum_of_movements() -> None:
    variant = ProductVariantFactory()
    InventoryMovementFactory(
        variant=variant,
        movement_type=InventoryMovement.MovementType.PURCHASE_RECEIVED,
        quantity_change=Decimal("25.000"),
    )
    InventoryMovementFactory(
        variant=variant,
        movement_type=InventoryMovement.MovementType.SALE,
        quantity_change=Decimal("-3.000"),
    )
    assert get_on_hand(variant) == Decimal("22.000")


def test_get_on_hand_ignores_other_variants() -> None:
    ProductVariantFactory()
    variant = ProductVariantFactory()
    InventoryMovementFactory(
        variant=variant,
        movement_type=InventoryMovement.MovementType.PURCHASE_RECEIVED,
        quantity_change=Decimal("7.500"),
    )
    assert get_on_hand(variant) == Decimal("7.500")


# ---------------------------------------------------------------------------
# receive_stock happy path
# ---------------------------------------------------------------------------


def test_receive_stock_creates_one_purchase_movement() -> None:
    variant = ProductVariantFactory()
    before = _purchase_count_for(variant)
    movement = receive_stock(variant=variant, quantity=Decimal("10.000"))
    assert _purchase_count_for(variant) == before + 1
    assert movement.movement_type == InventoryMovement.MovementType.PURCHASE_RECEIVED
    assert movement.quantity_change == Decimal("10.000")
    assert get_on_hand(variant) == Decimal("10.000")


def test_receive_stock_stores_quantity_with_three_decimals() -> None:
    variant = ProductVariantFactory(is_sold_loose=True)
    movement = receive_stock(variant=variant, quantity=Decimal("1.5"))
    movement.refresh_from_db()
    assert movement.quantity_change == Decimal("1.500")


def test_receive_stock_saves_supplier_batch_expiry_and_created_by() -> None:
    variant = ProductVariantFactory()
    supplier = SupplierFactory()
    user = User.objects.create_user(phone="+919876543210", password="temp")
    movement = receive_stock(
        variant=variant,
        quantity=Decimal("5"),
        supplier=supplier,
        batch_number="Batch-001",
        expiry_date=datetime.date(2026, 12, 31),
        created_by=user,
    )
    movement.refresh_from_db()
    assert movement.supplier_id == supplier.id
    assert movement.batch_number == "Batch-001"
    assert movement.expiry_date == datetime.date(2026, 12, 31)
    assert movement.created_by_id == user.id


def test_receive_stock_strips_reason() -> None:
    variant = ProductVariantFactory()
    movement = receive_stock(
        variant=variant,
        quantity=Decimal("1"),
        reason="  spoiled batch  ",
    )
    movement.refresh_from_db()
    assert movement.reason == "spoiled batch"


def test_receive_stock_returns_the_movement() -> None:
    variant = ProductVariantFactory()
    movement = receive_stock(variant=variant, quantity=Decimal("2"))
    assert isinstance(movement, InventoryMovement)
    assert movement.variant_id == variant.id


# ---------------------------------------------------------------------------
# quantity acceptance
# ---------------------------------------------------------------------------


def test_receive_stock_accepts_int() -> None:
    variant = ProductVariantFactory()
    movement = receive_stock(variant=variant, quantity=5)
    movement.refresh_from_db()
    assert movement.quantity_change == Decimal("5.000")


def test_receive_stock_accepts_decimal_on_loose_variant() -> None:
    variant = ProductVariantFactory(is_sold_loose=True)
    movement = receive_stock(variant=variant, quantity=Decimal("2.5"))
    movement.refresh_from_db()
    assert movement.quantity_change == Decimal("2.500")


def test_receive_stock_accepts_extra_trailing_zeros_on_loose_variant() -> None:
    variant = ProductVariantFactory(is_sold_loose=True)
    movement = receive_stock(variant=variant, quantity=Decimal("2.5000"))
    movement.refresh_from_db()
    assert movement.quantity_change == Decimal("2.500")


def test_receive_stock_accepts_a_past_expiry_date() -> None:
    variant = ProductVariantFactory()
    movement = receive_stock(
        variant=variant,
        quantity=Decimal("1"),
        expiry_date=datetime.date(2020, 1, 1),
    )
    movement.refresh_from_db()
    assert movement.expiry_date == datetime.date(2020, 1, 1)


# ---------------------------------------------------------------------------
# quantity rejections (each writes no movement)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "quantity",
    [
        2.5,
        True,
        "5",
        Decimal("NaN"),
        Decimal("Inf"),
        None,
    ],
)
def test_invalid_quantity_types_are_rejected_and_write_nothing(
    quantity: object,
) -> None:
    variant = ProductVariantFactory()
    before = _purchase_count_for(variant)
    with pytest.raises(InvalidQuantityError):
        receive_stock(variant=variant, quantity=quantity)
    assert _purchase_count_for(variant) == before


@pytest.mark.parametrize(
    "quantity",
    [
        Decimal("2.50001"),
        Decimal("2.5001"),
    ],
)
def test_quantity_with_more_than_three_decimals_is_rejected_and_writes_nothing(
    quantity: Decimal,
) -> None:
    variant = ProductVariantFactory()
    before = _purchase_count_for(variant)
    with pytest.raises(InvalidQuantityError):
        receive_stock(variant=variant, quantity=quantity)
    assert _purchase_count_for(variant) == before


def test_zero_quantity_is_rejected_and_writes_nothing() -> None:
    variant = ProductVariantFactory()
    before = _purchase_count_for(variant)
    with pytest.raises(InventoryError):
        receive_stock(variant=variant, quantity=Decimal("0"))
    assert _purchase_count_for(variant) == before


def test_negative_quantity_is_rejected_and_writes_nothing() -> None:
    variant = ProductVariantFactory()
    before = _purchase_count_for(variant)
    with pytest.raises(InventoryError):
        receive_stock(variant=variant, quantity=Decimal("-1"))
    assert _purchase_count_for(variant) == before


def test_quantity_at_max_absolute_is_rejected_and_writes_nothing() -> None:
    variant = ProductVariantFactory()
    before = _purchase_count_for(variant)
    with pytest.raises(InvalidQuantityError):
        receive_stock(variant=variant, quantity=Decimal("1000000000"))
    assert _purchase_count_for(variant) == before


def test_fractional_quantity_on_packet_variant_is_rejected_and_writes_nothing() -> None:
    variant = ProductVariantFactory(is_sold_loose=False)
    before = _purchase_count_for(variant)
    with pytest.raises(InvalidQuantityError):
        receive_stock(variant=variant, quantity=Decimal("1.5"))
    assert _purchase_count_for(variant) == before


def test_receive_stock_rejects_an_archived_supplier_and_writes_nothing() -> None:
    variant = ProductVariantFactory()
    supplier = SupplierFactory()
    supplier.is_active = False
    supplier.save()
    before = _purchase_count_for(variant)
    with pytest.raises(InventoryError):
        receive_stock(variant=variant, quantity=Decimal("1"), supplier=supplier)
    assert _purchase_count_for(variant) == before


def test_receive_stock_rejects_a_datetime_expiry_date_and_writes_nothing() -> None:
    variant = ProductVariantFactory()
    before = _purchase_count_for(variant)
    with pytest.raises(InventoryError):
        receive_stock(
            variant=variant,
            quantity=Decimal("1"),
            expiry_date=datetime.datetime(2026, 12, 31),
        )
    assert _purchase_count_for(variant) == before


def test_receive_stock_rejects_fractional_quantity_on_variant_switched_to_packet() -> None:
    # A loose variant on disk, then switched to packet through a queryset
    # update so the in-memory object is stale. The packet constraints require
    # unit PACKET and pack_size 1; read the ProductVariant constraints first so
    # the row stays valid when the update runs.
    ProductVariantFactory(is_sold_loose=False)
    loose = ProductVariantFactory(
        is_sold_loose=True,
        unit=ProductVariant.Unit.KG,
        pack_size=Decimal("1"),
    )
    ProductVariant.objects.filter(pk=loose.pk).update(
        is_sold_loose=False,
        unit=ProductVariant.Unit.PACKET,
        pack_size=Decimal("1"),
    )
    before = _purchase_count_for(loose)
    with pytest.raises(InvalidQuantityError):
        receive_stock(variant=loose, quantity=Decimal("1.5"))
    assert _purchase_count_for(loose) == before


def test_receive_stock_rejects_a_reason_longer_than_max_length() -> None:
    variant = ProductVariantFactory()
    before = _purchase_count_for(variant)
    with pytest.raises(InventoryError):
        receive_stock(
            variant=variant,
            quantity=Decimal("1"),
            reason="x" * (REASON_MAX_LENGTH + 1),
        )
    assert _purchase_count_for(variant) == before


def test_receive_stock_rejects_a_batch_number_longer_than_max_length() -> None:
    variant = ProductVariantFactory()
    before = _purchase_count_for(variant)
    with pytest.raises(InventoryError):
        receive_stock(
            variant=variant,
            quantity=Decimal("1"),
            batch_number="B" * (BATCH_MAX_LENGTH + 1),
        )
    assert _purchase_count_for(variant) == before


# ---------------------------------------------------------------------------
# stale variant: the service must use the locked, reloaded row
# ---------------------------------------------------------------------------


def test_receive_stock_uses_the_locked_reloaded_variant() -> None:
    # A packet variant on disk, then switched to loose through a queryset
    # update so the in-memory object is stale.
    ProductVariantFactory(is_sold_loose=False)
    variant = ProductVariantFactory(is_sold_loose=False, unit=ProductVariant.Unit.KG)
    ProductVariant.objects.filter(pk=variant.pk).update(is_sold_loose=True)

    movement = receive_stock(
        variant=variant,
        quantity=Decimal("1.5"),
    )
    movement.refresh_from_db()
    assert movement.quantity_change == Decimal("1.500")


# ---------------------------------------------------------------------------
# constant checks against the model
# ---------------------------------------------------------------------------


def test_reason_max_length_matches_the_model_field() -> None:
    assert InventoryMovement._meta.get_field("reason").max_length == REASON_MAX_LENGTH


def test_batch_max_length_matches_the_model_field() -> None:
    assert InventoryMovement._meta.get_field("batch_number").max_length == BATCH_MAX_LENGTH


# ---------------------------------------------------------------------------
# service module-level helpers
# ---------------------------------------------------------------------------


def test_clean_quantity_accepts_decimal_and_int() -> None:
    variant = ProductVariantFactory(is_sold_loose=True)
    assert _clean_quantity(Decimal("1.234"), variant=variant) == Decimal("1.234")
    assert _clean_quantity(1, variant=variant) == Decimal("1.000")


def test_clean_quantity_rejects_bool() -> None:
    variant = ProductVariantFactory(is_sold_loose=True)
    with pytest.raises(InvalidQuantityError):
        _clean_quantity(True, variant=variant)


def test_clean_quantity_rejects_float() -> None:
    variant = ProductVariantFactory(is_sold_loose=True)
    with pytest.raises(InvalidQuantityError):
        _clean_quantity(1.5, variant=variant)


def test_clean_quantity_rejects_nan_and_infinity() -> None:
    variant = ProductVariantFactory(is_sold_loose=True)
    with pytest.raises(InvalidQuantityError):
        _clean_quantity(Decimal("NaN"), variant=variant)
    with pytest.raises(InvalidQuantityError):
        _clean_quantity(Decimal("Inf"), variant=variant)


def test_clean_text_strips_whitespace_and_enforces_max_length() -> None:
    assert _clean_text("  hello  ", name="x", max_length=8, required=True) == "hello"
    with pytest.raises(InventoryError):
        _clean_text("x" * 9, name="x", max_length=8, required=False)


def test_clean_text_empty_required_is_an_error() -> None:
    with pytest.raises(InventoryError):
        _clean_text("   ", name="reason", max_length=8, required=True)
