"""Tests for the over-reserved check (Slice 34, D63)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.integrity import SEVERITY_ERROR, find_integrity_problems
from apps.inventory.models import StockReservation
from apps.inventory.tests.factories import (
    InventoryMovementFactory,
    StockReservationFactory,
)

pytestmark = pytest.mark.django_db


def _variant_with_stock(quantity: str):
    variant = ProductVariantFactory(is_sold_loose=False)
    InventoryMovementFactory(variant=variant, quantity_change=Decimal(quantity))
    return variant


def test_integrity_reports_a_variant_whose_holds_exceed_on_hand() -> None:
    variant = _variant_with_stock("2.000")
    StockReservationFactory(variant=variant, quantity=Decimal("3.000"))

    problems = find_integrity_problems()

    over_reserved = [problem for problem in problems if problem.code == "over_reserved"]
    assert len(over_reserved) == 1
    assert over_reserved[0].severity == SEVERITY_ERROR
    assert over_reserved[0].variant_sku == variant.sku_code
    assert "3.000" in over_reserved[0].message
    assert "2.000" in over_reserved[0].message


def test_integrity_ignores_a_variant_whose_holds_fit_in_on_hand() -> None:
    variant = _variant_with_stock("5.000")
    StockReservationFactory(variant=variant, quantity=Decimal("5.000"))

    assert find_integrity_problems() == []


def test_integrity_ignores_expired_checkout_holds() -> None:
    variant = _variant_with_stock("1.000")
    StockReservationFactory(
        variant=variant,
        quantity=Decimal("5.000"),
        kind=StockReservation.Kind.CHECKOUT_PENDING_PAYMENT,
        expires_at=timezone.now() - timedelta(minutes=1),
    )

    assert find_integrity_problems() == []


def test_check_inventory_integrity_command_prints_the_over_reserved_variant() -> None:
    variant = _variant_with_stock("1.000")
    StockReservationFactory(variant=variant, quantity=Decimal("2.000"))

    out = StringIO()
    with pytest.raises(CommandError):
        call_command("check_inventory_integrity", stdout=out)

    output = out.getvalue()
    assert "over_reserved" in output
    assert variant.sku_code in output
