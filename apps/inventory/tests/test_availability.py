"""Tests for availability: on-hand minus the online stock buffer (D55, D60)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.catalog.tests.factories import ProductVariantFactory
from apps.core.models import ShopSettings
from apps.core.services import get_shop_settings
from apps.inventory.availability import (
    get_available_quantities,
    get_available_quantity,
    get_held_quantities,
)
from apps.inventory.models import InventoryMovement, StockReservation
from apps.inventory.tests.factories import (
    InventoryMovementFactory,
    StockReservationFactory,
)

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


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


# ---------------------------------------------------------------------------
# Active reservations are taken off availability (Slice 34, D63)
# ---------------------------------------------------------------------------
def _held(
    variant,
    quantity: str,
    *,
    kind: str = StockReservation.Kind.HELD_FOR_FULFILMENT,
    expires_at: datetime | None = None,
):
    return StockReservationFactory(
        variant=variant,
        quantity=Decimal(quantity),
        kind=kind,
        expires_at=expires_at,
    )


def test_active_held_reservations_are_subtracted() -> None:
    variant = ProductVariantFactory()
    _stock(variant, "10.000")
    _held(variant, "3.000")

    assert get_available_quantity(variant, now=NOW) == Decimal("7.000")


def test_released_reservations_are_ignored() -> None:
    variant = ProductVariantFactory()
    _stock(variant, "10.000")
    StockReservationFactory(
        variant=variant,
        quantity=Decimal("3.000"),
        status=StockReservation.Status.RELEASED,
        released_at=NOW,
    )

    assert get_available_quantity(variant, now=NOW) == Decimal("10.000")


def test_consumed_reservations_are_ignored() -> None:
    variant = ProductVariantFactory()
    _stock(variant, "10.000")
    StockReservationFactory(
        variant=variant,
        quantity=Decimal("3.000"),
        status=StockReservation.Status.CONSUMED,
        consumed_at=NOW,
    )

    assert get_available_quantity(variant, now=NOW) == Decimal("10.000")


def test_unexpired_checkout_reservations_are_subtracted() -> None:
    variant = ProductVariantFactory()
    _stock(variant, "10.000")
    _held(
        variant,
        "3.000",
        kind=StockReservation.Kind.CHECKOUT_PENDING_PAYMENT,
        expires_at=NOW + timedelta(minutes=5),
    )

    assert get_available_quantity(variant, now=NOW) == Decimal("7.000")


def test_expired_checkout_reservations_are_ignored_before_the_job_runs() -> None:
    variant = ProductVariantFactory()
    _stock(variant, "10.000")
    _held(
        variant,
        "3.000",
        kind=StockReservation.Kind.CHECKOUT_PENDING_PAYMENT,
        expires_at=NOW - timedelta(minutes=1),
    )

    assert get_available_quantity(variant, now=NOW) == Decimal("10.000")


def test_a_checkout_reservation_expiring_exactly_now_is_ignored() -> None:
    variant = ProductVariantFactory()
    _stock(variant, "10.000")
    _held(
        variant,
        "3.000",
        kind=StockReservation.Kind.CHECKOUT_PENDING_PAYMENT,
        expires_at=NOW,
    )

    assert get_available_quantity(variant, now=NOW) == Decimal("10.000")


def test_held_reservations_never_expire() -> None:
    variant = ProductVariantFactory()
    _stock(variant, "10.000")
    _held(variant, "3.000")

    later = NOW + timedelta(days=365)

    assert get_available_quantity(variant, now=later) == Decimal("7.000")


def test_several_reservations_of_one_variant_add_up() -> None:
    variant = ProductVariantFactory()
    _stock(variant, "10.000")
    _held(variant, "2.000")
    _held(variant, "3.000")

    assert get_available_quantity(variant, now=NOW) == Decimal("5.000")


def test_reservations_of_other_variants_do_not_matter() -> None:
    reserved = ProductVariantFactory()
    _stock(reserved, "10.000")
    _held(reserved, "9.000")

    other = ProductVariantFactory()
    _stock(other, "10.000")

    assert get_available_quantity(other, now=NOW) == Decimal("10.000")


def test_available_is_zero_when_holds_exceed_the_stock_after_the_buffer() -> None:
    _set_buffer(2)
    variant = ProductVariantFactory()
    _stock(variant, "5.000")
    _held(variant, "10.000")

    assert get_available_quantity(variant, now=NOW) == Decimal("0.000")


def test_loose_holds_keep_three_decimals() -> None:
    variant = ProductVariantFactory(
        is_sold_loose=True,
        unit="KG",
        pack_size=Decimal("1.000"),
        quantity_step=Decimal("0.001"),
    )
    _stock(variant, "2.500")
    _held(variant, "1.234")

    available = get_available_quantity(variant, now=NOW)

    assert available == Decimal("1.266")
    assert available.as_tuple().exponent == -3


def test_query_count_with_holds_is_constant_for_one_and_ten_variants() -> None:
    variants = [ProductVariantFactory() for _ in range(10)]
    for variant in variants:
        _stock(variant, "5.000")
        _held(variant, "1.000")
    # Warm the settings cache, so only the two grouped queries are measured.
    get_shop_settings()

    with CaptureQueriesContext(connection) as one:
        get_available_quantities([variants[0].pk], now=NOW)
    with CaptureQueriesContext(connection) as ten:
        get_available_quantities([variant.pk for variant in variants], now=NOW)

    assert len(one.captured_queries) == len(ten.captured_queries)


def test_get_held_quantities_matches_the_sum_per_variant() -> None:
    first = ProductVariantFactory()
    second = ProductVariantFactory()
    _held(first, "2.000")
    _held(first, "3.000")
    _held(second, "4.000")
    _held(
        first,
        "5.000",
        kind=StockReservation.Kind.CHECKOUT_PENDING_PAYMENT,
        expires_at=NOW - timedelta(minutes=1),
    )

    held = get_held_quantities([first.pk, second.pk], now=NOW)

    assert held == {first.pk: Decimal("5.000"), second.pk: Decimal("4.000")}


def test_get_held_quantities_returns_zero_for_variants_without_holds() -> None:
    variant = ProductVariantFactory()

    assert get_held_quantities([variant.pk], now=NOW) == {variant.pk: Decimal("0.000")}
