"""Tests for inventory services: adjust_stock, record_damage, count_stock (Slice 16c)."""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.models import InventoryMovement
from apps.inventory.services import (
    InsufficientStockError,
    InvalidQuantityError,
    InventoryError,
    adjust_stock,
    count_stock,
    get_on_hand,
    record_damage,
)

User = get_user_model()

pytestmark = pytest.mark.django_db


def _purchase_count_for(variant: ProductVariant) -> int:
    return InventoryMovement.objects.filter(
        variant=variant, movement_type=InventoryMovement.MovementType.PURCHASE_RECEIVED
    ).count()


def _adjustment_count_for(variant: ProductVariant) -> int:
    return InventoryMovement.objects.filter(
        variant=variant, movement_type=InventoryMovement.MovementType.ADJUSTMENT
    ).count()


def _damage_count_for(variant: ProductVariant) -> int:
    return InventoryMovement.objects.filter(
        variant=variant, movement_type=InventoryMovement.MovementType.DAMAGE
    ).count()


def _set_on_hand(variant: ProductVariant, amount: Decimal) -> None:
    InventoryMovement.objects.create(
        variant=variant,
        movement_type=(
            InventoryMovement.MovementType.ADJUSTMENT
            if amount < 0
            else InventoryMovement.MovementType.PURCHASE_RECEIVED
        ),
        quantity_change=amount,
        reason="test setup" if amount < 0 else "",
    )


# ---------------------------------------------------------------------------
# adjust_stock
# ---------------------------------------------------------------------------


def test_adjust_stock_positive_changes_on_hand() -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("10.000"))
    movement = adjust_stock(variant=variant, quantity_change=Decimal("3"), reason="found more")
    movement.refresh_from_db()
    assert get_on_hand(variant) == Decimal("13.000")
    assert movement.quantity_change == Decimal("3.000")


def test_adjust_stock_negative_changes_on_hand() -> None:
    variant = ProductVariantFactory(is_sold_loose=True)
    _set_on_hand(variant, Decimal("10.000"))
    movement = adjust_stock(
        variant=variant, quantity_change=Decimal("-2.5"), reason="miscount write-off"
    )
    movement.refresh_from_db()
    assert get_on_hand(variant) == Decimal("7.500")
    assert movement.quantity_change == Decimal("-2.500")


def test_adjust_stock_reason_is_stripped() -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("10.000"))
    movement = adjust_stock(
        variant=variant, quantity_change=Decimal("1"), reason="  found 1 more  "
    )
    movement.refresh_from_db()
    assert movement.reason == "found 1 more"


def test_adjust_stock_stores_created_by() -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("10.000"))
    user = User.objects.create_user(phone="+919876543210", password="temp")
    movement = adjust_stock(
        variant=variant, quantity_change=Decimal("1"), reason="counted", created_by=user
    )
    movement.refresh_from_db()
    assert movement.created_by_id == user.id


def test_adjust_stock_type_is_adjustment_with_no_supplier() -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("10.000"))
    movement = adjust_stock(variant=variant, quantity_change=Decimal("1"), reason="ok")
    movement.refresh_from_db()
    assert movement.movement_type == InventoryMovement.MovementType.ADJUSTMENT
    assert movement.supplier_id is None
    assert movement.batch_number == ""
    assert movement.expiry_date is None


@pytest.mark.parametrize(
    "reason",
    [
        "",
        "   ",
    ],
)
def test_adjust_stock_rejects_empty_or_whitespace_only_reason(reason: str) -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("10.000"))
    with pytest.raises(InventoryError):
        adjust_stock(variant=variant, quantity_change=Decimal("1"), reason=reason)


def test_adjust_stock_rejects_zero() -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("10.000"))
    before = _adjustment_count_for(variant)
    with pytest.raises(InventoryError):
        adjust_stock(variant=variant, quantity_change=Decimal("0"), reason="ok")
    assert _adjustment_count_for(variant) == before


def test_adjust_stock_rejects_float() -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("10.000"))
    with pytest.raises(InvalidQuantityError):
        adjust_stock(variant=variant, quantity_change=2.5, reason="ok")
    assert _adjustment_count_for(variant) == 0


def test_adjust_stock_rejects_fractional_on_packet() -> None:
    variant = ProductVariantFactory(is_sold_loose=False)
    _set_on_hand(variant, Decimal("10.000"))
    with pytest.raises(InvalidQuantityError):
        adjust_stock(variant=variant, quantity_change=Decimal("1.5"), reason="ok")
    assert _adjustment_count_for(variant) == 0


def test_adjust_stock_accepts_fractional_on_loose() -> None:
    variant = ProductVariantFactory(is_sold_loose=True)
    _set_on_hand(variant, Decimal("10.000"))
    movement = adjust_stock(variant=variant, quantity_change=Decimal("1.5"), reason="counted")
    movement.refresh_from_db()
    assert movement.quantity_change == Decimal("1.500")


def test_adjust_stock_allows_below_zero_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("2.000"))
    with caplog.at_level("WARNING"):
        adjust_stock(variant=variant, quantity_change=Decimal("-5"), reason="write-off")
    assert "Adjusting variant pk=" in caplog.text
    assert get_on_hand(variant) == Decimal("-3.000")


def test_adjust_stock_does_not_warn_when_on_hand_stays_non_negative(
    caplog: pytest.LogCaptureFixture,
) -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("10.000"))
    with caplog.at_level("WARNING"):
        adjust_stock(variant=variant, quantity_change=Decimal("-3"), reason="ok")
    assert "Adjusting variant pk=" not in caplog.text


# ---------------------------------------------------------------------------
# record_damage
# ---------------------------------------------------------------------------


def test_record_damage_stores_negative_damage_movement() -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("10.000"))
    movement = record_damage(variant=variant, quantity=Decimal("2"), reason="broken")
    movement.refresh_from_db()
    assert movement.movement_type == InventoryMovement.MovementType.DAMAGE
    assert movement.quantity_change == Decimal("-2.000")
    assert get_on_hand(variant) == Decimal("8.000")


def test_record_damage_reason_is_required() -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("10.000"))
    with pytest.raises(InventoryError):
        record_damage(variant=variant, quantity=Decimal("1"), reason="   ")


def test_record_damage_rejects_zero_or_negative() -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("10.000"))
    with pytest.raises(InventoryError):
        record_damage(variant=variant, quantity=Decimal("0"), reason="ok")
    with pytest.raises(InventoryError):
        record_damage(variant=variant, quantity=Decimal("-1"), reason="ok")
    assert _damage_count_for(variant) == 0


def test_record_damage_rejects_more_than_on_hand_and_writes_nothing() -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("3.000"))
    with pytest.raises(InsufficientStockError):
        record_damage(variant=variant, quantity=Decimal("5"), reason="broken")
    assert _damage_count_for(variant) == 0


def test_record_damage_allows_exact_on_hand() -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("4.000"))
    movement = record_damage(variant=variant, quantity=Decimal("4"), reason="all broken")
    movement.refresh_from_db()
    assert get_on_hand(variant) == Decimal("0.000")
    assert movement.quantity_change == Decimal("-4.000")


def test_record_damage_rejects_any_damage_when_on_hand_is_already_negative() -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("-1.000"))
    with pytest.raises(InsufficientStockError, match="already negative"):
        record_damage(variant=variant, quantity=Decimal("1"), reason="broken")
    assert _damage_count_for(variant) == 0


def test_record_damage_accepts_loose_decimal() -> None:
    variant = ProductVariantFactory(is_sold_loose=True)
    _set_on_hand(variant, Decimal("2.500"))
    movement = record_damage(variant=variant, quantity=Decimal("0.5"), reason="spoiled 500g")
    movement.refresh_from_db()
    assert movement.quantity_change == Decimal("-0.500")
    assert get_on_hand(variant) == Decimal("2.000")


def test_record_damage_rejects_fractional_on_packet() -> None:
    variant = ProductVariantFactory(is_sold_loose=False)
    _set_on_hand(variant, Decimal("10.000"))
    with pytest.raises(InvalidQuantityError):
        record_damage(variant=variant, quantity=Decimal("1.5"), reason="broken")
    assert _damage_count_for(variant) == 0


# ---------------------------------------------------------------------------
# count_stock
# ---------------------------------------------------------------------------


def test_count_stock_above_on_hand_writes_positive_adjustment() -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("10.000"))
    movement = count_stock(variant=variant, counted_quantity=Decimal("13"), reason="counted")
    movement.refresh_from_db()
    assert movement.quantity_change == Decimal("3.000")
    assert get_on_hand(variant) == Decimal("13.000")


def test_count_stock_below_on_hand_writes_negative_adjustment() -> None:
    variant = ProductVariantFactory(is_sold_loose=True)
    _set_on_hand(variant, Decimal("10.000"))
    movement = count_stock(variant=variant, counted_quantity=Decimal("7.5"), reason="counted")
    movement.refresh_from_db()
    assert movement.quantity_change == Decimal("-2.500")
    assert get_on_hand(variant) == Decimal("7.500")


def test_count_stock_equal_returns_none_and_writes_nothing() -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("9.000"))
    before = _adjustment_count_for(variant)
    result = count_stock(variant=variant, counted_quantity=Decimal("9"), reason="counted")
    assert result is None
    assert _adjustment_count_for(variant) == before


def test_count_stock_zero_is_allowed() -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("5.000"))
    movement = count_stock(variant=variant, counted_quantity=Decimal("0"), reason="counted")
    movement.refresh_from_db()
    assert get_on_hand(variant) == Decimal("0.000")


def test_count_stock_rejects_negative_counted() -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("5.000"))
    with pytest.raises(InventoryError):
        count_stock(variant=variant, counted_quantity=Decimal("-1"), reason="counted")
    assert _adjustment_count_for(variant) == 0


def test_count_stock_requires_reason_even_when_equal() -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("5.000"))
    before = _adjustment_count_for(variant)
    with pytest.raises(InventoryError):
        count_stock(variant=variant, counted_quantity=Decimal("5"), reason="   ")
    assert _adjustment_count_for(variant) == before


def test_count_stock_accepts_loose_decimal() -> None:
    variant = ProductVariantFactory(is_sold_loose=True)
    _set_on_hand(variant, Decimal("10.000"))
    movement = count_stock(variant=variant, counted_quantity=Decimal("10.5"), reason="counted")
    movement.refresh_from_db()
    assert movement.quantity_change == Decimal("0.500")
    assert get_on_hand(variant) == Decimal("10.500")


def test_count_stock_rejects_fractional_counted_on_packet() -> None:
    variant = ProductVariantFactory(is_sold_loose=False)
    _set_on_hand(variant, Decimal("10.000"))
    with pytest.raises(InvalidQuantityError):
        count_stock(variant=variant, counted_quantity=Decimal("10.5"), reason="counted")
    assert _adjustment_count_for(variant) == 0


def test_count_stock_stores_created_by() -> None:
    variant = ProductVariantFactory()
    _set_on_hand(variant, Decimal("10.000"))
    user = User.objects.create_user(phone="+919876543210", password="temp")
    movement = count_stock(
        variant=variant, counted_quantity=Decimal("12"), reason="counted", created_by=user
    )
    movement.refresh_from_db()
    assert movement.created_by_id == user.id
