"""Tests for the check_inventory_integrity management command (Slice 16f)."""

from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.models import InventoryMovement

pytestmark = pytest.mark.django_db


def _packet_variant() -> ProductVariant:
    return ProductVariantFactory(is_sold_loose=False)


def _purchase(variant, qty, **kwargs):
    return InventoryMovement.objects.create(
        variant=variant,
        movement_type=InventoryMovement.MovementType.PURCHASE_RECEIVED,
        quantity_change=qty,
        **kwargs,
    )


def _adjustment(variant, qty, reason="count", **kwargs):
    return InventoryMovement.objects.create(
        variant=variant,
        movement_type=InventoryMovement.MovementType.ADJUSTMENT,
        quantity_change=qty,
        reason=reason,
        **kwargs,
    )


def _row_count():
    return InventoryMovement.objects.count()


# ---------------------------------------------------------------------------
# Clean ledger
# ---------------------------------------------------------------------------


def test_clean_ledger_prints_no_problems_found() -> None:
    variant = _packet_variant()
    _purchase(variant, Decimal("10"))
    _adjustment(variant, Decimal("-2"), reason="shrinkage")

    out = StringIO()
    call_command("check_inventory_integrity", stdout=out)

    output = out.getvalue()
    assert "no problems found" in output
    assert "error(s)" not in output


# ---------------------------------------------------------------------------
# ERROR case
# ---------------------------------------------------------------------------


def test_error_ledger_raises_and_reports() -> None:
    variant = _packet_variant()
    _purchase(variant, Decimal("1.500"))

    out = StringIO()
    with pytest.raises(CommandError, match="1 error"):
        call_command("check_inventory_integrity", stdout=out)

    output = out.getvalue()
    assert "fractional_on_packet" in output
    assert variant.sku_code in output
    assert "1 error(s), 0 warning(s)" in output


# ---------------------------------------------------------------------------
# WARNING-only case
# ---------------------------------------------------------------------------


def test_warning_only_ledger_does_not_raise() -> None:
    variant = _packet_variant()
    _purchase(variant, Decimal("2"))
    _adjustment(variant, Decimal("-5"), reason="write-off")

    out = StringIO()
    call_command("check_inventory_integrity", stdout=out)

    output = out.getvalue()
    assert "negative_on_hand" in output
    assert "0 error(s), 1 warning(s)" in output


# ---------------------------------------------------------------------------
# Read-only
# ---------------------------------------------------------------------------


def test_command_does_not_create_or_delete_rows_in_error_case() -> None:
    variant = _packet_variant()
    _purchase(variant, Decimal("1.500"))

    before = _row_count()
    out = StringIO()
    with pytest.raises(CommandError):
        call_command("check_inventory_integrity", stdout=out)
    after = _row_count()

    assert before == after


def test_command_does_not_create_or_delete_rows_in_clean_case() -> None:
    variant = _packet_variant()
    _purchase(variant, Decimal("10"))

    before = _row_count()
    out = StringIO()
    call_command("check_inventory_integrity", stdout=out)
    after = _row_count()

    assert before == after
