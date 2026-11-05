"""Tests for availability: on-hand minus the online stock buffer (D55, D60)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.catalog.tests.factories import ProductVariantFactory
from apps.core.models import ShopSettings
from apps.core.services import get_shop_settings
from apps.inventory.availability import get_available_quantities, get_available_quantity
from apps.inventory.models import InventoryMovement
from apps.inventory.tests.factories import InventoryMovementFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """The locmem cache outlives a test's database transaction, so empty it."""
    cache.clear()
    yield
    cache.clear()


def _set_buffer(value: int) -> None:
    row = ShopSettings.load()
    row.online_stock_buffer = value
    row.save(update_fields=["online_stock_buffer", "updated_at"])


def _stock(variant, quantity: str) -> None:
    InventoryMovementFactory(variant=variant, quantity_change=Decimal(quantity))


def test_available_is_on_hand_when_the_buffer_is_zero() -> None:
    variant = ProductVariantFactory()
    _stock(variant, "10.000")

    assert get_available_quantity(variant) == Decimal("10.000")


def test_available_subtracts_the_online_stock_buffer() -> None:
    _set_buffer(3)
    variant = ProductVariantFactory()
    _stock(variant, "10.000")

    assert get_available_quantity(variant) == Decimal("7.000")


def test_available_never_goes_below_zero_when_the_buffer_is_larger() -> None:
    _set_buffer(5)
    variant = ProductVariantFactory()
    _stock(variant, "2.000")

    assert get_available_quantity(variant) == Decimal("0.000")


def test_available_is_zero_when_on_hand_is_negative() -> None:
    variant = ProductVariantFactory()
    InventoryMovementFactory(
        variant=variant,
        movement_type=InventoryMovement.MovementType.ADJUSTMENT,
        quantity_change=Decimal("-3.000"),
        reason="Stock count found less",
    )

    assert get_available_quantity(variant) == Decimal("0.000")


def test_available_is_zero_for_a_variant_without_movements() -> None:
    variant = ProductVariantFactory()

    assert get_available_quantity(variant) == Decimal("0.000")


def test_available_keeps_three_decimals_for_loose_variants() -> None:
    variant = ProductVariantFactory(
        is_sold_loose=True,
        unit="KG",
        pack_size=Decimal("1.000"),
        quantity_step=Decimal("0.001"),
    )
    _stock(variant, "2.500")

    available = get_available_quantity(variant)

    assert available == Decimal("2.500")
    assert available.as_tuple().exponent == -3


def test_available_quantities_matches_the_single_call_for_every_variant() -> None:
    first = ProductVariantFactory()
    second = ProductVariantFactory()
    _stock(first, "4.000")
    _stock(second, "6.000")

    result = get_available_quantities([first.pk, second.pk])

    assert result[first.pk] == get_available_quantity(first)
    assert result[second.pk] == get_available_quantity(second)
    assert result[first.pk] == Decimal("4.000")
    assert result[second.pk] == Decimal("6.000")


def test_available_quantities_uses_the_same_query_count_for_one_and_ten_variants() -> None:
    variants = [ProductVariantFactory() for _ in range(10)]
    for variant in variants:
        _stock(variant, "1.000")
    # Warm the settings cache, so only the movement query is measured.
    get_shop_settings()

    with CaptureQueriesContext(connection) as one:
        get_available_quantities([variants[0].pk])
    with CaptureQueriesContext(connection) as ten:
        get_available_quantities([variant.pk for variant in variants])

    assert len(one.captured_queries) == len(ten.captured_queries)


def test_available_quantities_returns_an_empty_dict_for_no_variants() -> None:
    assert get_available_quantities([]) == {}


def test_available_quantities_ignores_duplicate_ids() -> None:
    variant = ProductVariantFactory()
    _stock(variant, "5.000")

    result = get_available_quantities([variant.pk, variant.pk])

    assert result == {variant.pk: Decimal("5.000")}


def test_available_reads_the_buffer_saved_in_shop_settings() -> None:
    _set_buffer(2)
    cache.clear()
    variant = ProductVariantFactory()
    _stock(variant, "9.000")

    assert get_available_quantity(variant) == Decimal("7.000")
