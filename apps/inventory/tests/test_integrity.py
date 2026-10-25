"""Tests for the read-only ledger integrity check (Slice 16e)."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.integrity import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    IntegrityProblem,
    find_integrity_problems,
)
from apps.inventory.models import InventoryMovement
from apps.inventory.tests.factories import (
    InventoryMovementFactory,
    SupplierFactory,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _packet_variant() -> ProductVariant:
    return ProductVariantFactory(is_sold_loose=False)


def _loose_variant() -> ProductVariant:
    return ProductVariantFactory(
        is_sold_loose=True,
        unit=ProductVariant.Unit.KG,
        pack_size=Decimal("1"),
    )


def _purchase(variant: ProductVariant, qty: Decimal) -> InventoryMovement:
    return InventoryMovementFactory(
        variant=variant,
        movement_type=InventoryMovement.MovementType.PURCHASE_RECEIVED,
        quantity_change=qty,
    )


def _adjustment(
    variant: ProductVariant,
    qty: Decimal,
    reason: str = "count",
) -> InventoryMovement:
    return InventoryMovement.objects.create(
        variant=variant,
        movement_type=InventoryMovement.MovementType.ADJUSTMENT,
        quantity_change=qty,
        reason=reason,
    )


def _damage(
    variant: ProductVariant,
    qty: Decimal,
    reason: str = "broken",
) -> InventoryMovement:
    return InventoryMovement.objects.create(
        variant=variant,
        movement_type=InventoryMovement.MovementType.DAMAGE,
        quantity_change=-qty,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Empty and clean ledgers
# ---------------------------------------------------------------------------


def test_empty_ledger_returns_nothing() -> None:
    assert find_integrity_problems() == []


def test_clean_ledger_returns_nothing() -> None:
    variant = _packet_variant()
    _purchase(variant, Decimal("10"))
    _damage(variant, Decimal("2"))
    _adjustment(variant, Decimal("1"), reason="count")

    problems = find_integrity_problems()
    assert problems == []


# ---------------------------------------------------------------------------
# fractional_on_packet
# ---------------------------------------------------------------------------


def test_fractional_on_packet_is_reported() -> None:
    variant = _packet_variant()
    movement = _purchase(variant, Decimal("1.500"))

    problems = find_integrity_problems()
    assert len(problems) == 1
    problem = problems[0]
    assert problem.code == "fractional_on_packet"
    assert problem.severity == SEVERITY_ERROR
    assert problem.variant_sku == variant.sku_code
    assert problem.movement_id == movement.id


def test_fractional_on_loose_variant_is_not_reported() -> None:
    variant = _loose_variant()
    _purchase(variant, Decimal("1.500"))

    problems = find_integrity_problems()
    assert problems == []


def test_whole_number_on_packet_is_not_reported() -> None:
    variant = _packet_variant()
    _purchase(variant, Decimal("15"))

    problems = find_integrity_problems()
    assert problems == []


# ---------------------------------------------------------------------------
# blank_adjustment_reason
# ---------------------------------------------------------------------------


def test_blank_adjustment_reason_with_spaces_is_reported() -> None:
    variant = _packet_variant()
    movement = _adjustment(variant, Decimal("1"), reason="   ")

    problems = find_integrity_problems()
    assert len(problems) == 1
    assert problems[0].code == "blank_adjustment_reason"
    assert problems[0].movement_id == movement.pk


def test_blank_adjustment_reason_with_whitespace_chars_is_reported() -> None:
    variant = _packet_variant()
    movement = _adjustment(variant, Decimal("1"), reason="\t\n")

    problems = find_integrity_problems()
    assert len(problems) == 1
    assert problems[0].code == "blank_adjustment_reason"
    assert problems[0].movement_id == movement.pk


def test_adjustment_with_a_real_reason_is_not_reported() -> None:
    variant = _packet_variant()
    _adjustment(variant, Decimal("1"), reason="count")

    problems = find_integrity_problems()
    assert problems == []


# ---------------------------------------------------------------------------
# supplier_on_non_purchase
# ---------------------------------------------------------------------------


def test_supplier_on_adjustment_is_reported() -> None:
    variant = _packet_variant()
    supplier = SupplierFactory()
    movement = InventoryMovement.objects.create(
        variant=variant,
        movement_type=InventoryMovement.MovementType.ADJUSTMENT,
        quantity_change=Decimal("1"),
        reason="count",
        supplier=supplier,
    )

    problems = find_integrity_problems()
    assert len(problems) == 1
    assert problems[0].code == "supplier_on_non_purchase"
    assert problems[0].movement_id == movement.pk


def test_supplier_on_purchase_is_not_reported() -> None:
    variant = _packet_variant()
    supplier = SupplierFactory()
    InventoryMovement.objects.create(
        variant=variant,
        movement_type=InventoryMovement.MovementType.PURCHASE_RECEIVED,
        quantity_change=Decimal("1"),
        supplier=supplier,
    )

    problems = find_integrity_problems()
    assert problems == []


# ---------------------------------------------------------------------------
# negative_on_hand
# ---------------------------------------------------------------------------


def test_negative_on_hand_is_a_warning() -> None:
    variant = _packet_variant()
    _purchase(variant, Decimal("2"))
    _adjustment(variant, Decimal("-5"), reason="test")

    problems = find_integrity_problems()
    assert len(problems) == 1
    problem = problems[0]
    assert problem.code == "negative_on_hand"
    assert problem.severity == SEVERITY_WARNING
    assert problem.variant_sku == variant.sku_code
    assert problem.movement_id is None


def test_zero_on_hand_is_not_reported() -> None:
    variant = _packet_variant()
    _purchase(variant, Decimal("5"))
    _adjustment(variant, Decimal("-5"), reason="test")

    problems = find_integrity_problems()
    assert problems == []


def test_positive_on_hand_is_not_reported() -> None:
    variant = _packet_variant()
    _purchase(variant, Decimal("5"))

    problems = find_integrity_problems()
    assert problems == []


# ---------------------------------------------------------------------------
# ordering
# ---------------------------------------------------------------------------


def test_error_comes_before_warning() -> None:
    bad_variant = _packet_variant()
    _purchase(bad_variant, Decimal("1.500"))

    good_variant = _packet_variant()
    _purchase(good_variant, Decimal("2"))
    _adjustment(good_variant, Decimal("-5"), reason="test")

    problems = find_integrity_problems()
    assert len(problems) == 2
    assert problems[0].severity == SEVERITY_ERROR
    assert problems[1].severity == SEVERITY_WARNING


# ---------------------------------------------------------------------------
# Two different variants
# ---------------------------------------------------------------------------


def test_two_problems_on_two_variants_are_both_reported() -> None:
    variant_a = _packet_variant()
    _purchase(variant_a, Decimal("1.500"))

    variant_b = _packet_variant()
    _purchase(variant_b, Decimal("2.500"))

    problems = find_integrity_problems()
    assert len(problems) == 2
    skus = {problem.variant_sku for problem in problems}
    assert skus == {variant_a.sku_code, variant_b.sku_code}


# ---------------------------------------------------------------------------
# Frozen dataclass
# ---------------------------------------------------------------------------


def test_integrity_problem_is_frozen() -> None:
    variant = _packet_variant()
    movement = _purchase(variant, Decimal("1.500"))

    problem = IntegrityProblem(
        code="fractional_on_packet",
        severity=SEVERITY_ERROR,
        variant_sku=variant.sku_code,
        movement_id=movement.pk,
        message="test",
    )

    with pytest.raises(FrozenInstanceError):
        problem.code = "other"
