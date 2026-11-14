"""Stock valuation: on-hand times today's cost price (D85)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.catalog.tests.factories import BrandFactory, CategoryFactory
from apps.inventory.models import InventoryMovement
from apps.inventory.tests.factories import InventoryMovementFactory
from apps.orders.tests.staff_helpers import staff
from apps.reports.queries import (
    ReportsPermissionError,
    ValuationGroup,
    stock_valuation_report,
)
from apps.reports.tests.helpers import make_variant

pytestmark = pytest.mark.django_db


def test_one_row_per_variant_with_stock_and_a_cost() -> None:
    variant = make_variant(name="Atta", cost="50.00", stock="10.000")

    report = stock_valuation_report(staff())

    assert [row.sku_code for row in report.rows] == [variant.sku_code]
    assert report.rows[0].on_hand == Decimal("10.000")
    assert report.rows[0].value == Decimal("500.00")


def test_the_brand_subtotal_adds_its_variants() -> None:
    brand = BrandFactory(name="Nestle")
    make_variant(name="KitKat", brand=brand, cost="40.00", stock="2.000")
    make_variant(name="Munch", brand=brand, cost="10.00", stock="3.000")

    report = stock_valuation_report(staff())

    assert report.brand_totals == [ValuationGroup(label="Nestle", value=Decimal("110.00"))]


def test_the_category_subtotal_adds_its_variants() -> None:
    category = CategoryFactory(name="Dairy")
    make_variant(name="Milk", category=category, cost="30.00", stock="2.000")

    report = stock_valuation_report(staff())

    assert report.category_totals[0].label == "Dairy"
    assert report.category_totals[0].value == Decimal("60.00")


def test_the_grand_total_adds_every_row() -> None:
    make_variant(name="Atta", cost="50.00", stock="2.000")
    make_variant(name="Rice", cost="25.00", stock="4.000")

    report = stock_valuation_report(staff())

    assert report.grand_total == Decimal("200.00")
    assert report.grand_total == sum((row.value for row in report.rows), Decimal("0.00"))


def test_a_variant_without_a_cost_is_left_out_and_counted() -> None:
    make_variant(name="No cost", cost=None, stock="5.000")
    make_variant(name="With cost", cost="10.00", stock="5.000")

    report = stock_valuation_report(staff())

    assert [row.name for row in report.rows] == ["With cost"]
    assert report.grand_total == Decimal("50.00")
    assert report.blank_cost_count == 1
    assert report.blank_cost_variants[0].name == "No cost"


def test_a_loose_weight_keeps_three_decimals() -> None:
    make_variant(name="Chana dal", cost="100.00", stock="2.500", loose=True)

    report = stock_valuation_report(staff())

    assert report.rows[0].on_hand == Decimal("2.500")
    assert report.rows[0].value == Decimal("250.00")


def test_a_variant_with_nothing_on_the_shelf_is_left_out() -> None:
    make_variant(name="Empty", cost="10.00", stock=None)

    report = stock_valuation_report(staff())

    assert report.rows == []
    assert report.grand_total == Decimal("0.00")


def test_a_variant_with_less_than_nothing_on_the_shelf_is_left_out() -> None:
    variant = make_variant(name="Short", cost="10.00", stock="1.000")
    # An adjustment may carry either sign, which is how on-hand can go below zero.
    InventoryMovementFactory(
        variant=variant,
        movement_type=InventoryMovement.MovementType.ADJUSTMENT,
        quantity_change=Decimal("-3.000"),
        reason="Count",
    )

    report = stock_valuation_report(staff())

    assert report.rows == []
    assert report.grand_total == Decimal("0.00")


def test_a_user_without_the_capability_cannot_read_the_valuation() -> None:
    with pytest.raises(ReportsPermissionError):
        stock_valuation_report(staff("PACKER"))
