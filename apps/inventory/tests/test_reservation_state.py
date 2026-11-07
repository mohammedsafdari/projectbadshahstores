"""Tests for hold, release, consume and expire (Slice 34, D63)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache

from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.availability import get_available_quantity
from apps.inventory.models import InventoryMovement, StockReservation
from apps.inventory.reservations import (
    ReservationExpiredError,
    ReservationNotFoundError,
    ReservationStateError,
    consume_reservation,
    expire_stale_reservations,
    hold_for_fulfilment,
    release_group,
)
from apps.inventory.services import get_on_hand
from apps.inventory.tests.factories import (
    InventoryMovementFactory,
    StockReservationFactory,
)

pytestmark = pytest.mark.django_db

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=IST)
LATER = NOW + timedelta(minutes=10)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """The locmem cache outlives a test's database transaction, so empty it."""
    cache.clear()
    yield
    cache.clear()


def _variant(stock: str = "10.000") -> ProductVariant:
    variant = ProductVariantFactory(
        is_sold_loose=False,
        unit=ProductVariant.Unit.PACKET,
        pack_size=Decimal("1.000"),
        quantity_step=Decimal("1"),
    )
    InventoryMovementFactory(variant=variant, quantity_change=Decimal(stock))
    return variant


def _row(
    group,
    *,
    kind: str = StockReservation.Kind.HELD_FOR_FULFILMENT,
    expires_at: datetime | None = None,
    status: str = StockReservation.Status.ACTIVE,
    quantity: str = "1.000",
    variant: ProductVariant | None = None,
):
    return StockReservationFactory(
        checkout_group=group,
        variant=variant if variant is not None else _variant(),
        quantity=Decimal(quantity),
        kind=kind,
        expires_at=expires_at,
        status=status,
        consumed_at=NOW if status == StockReservation.Status.CONSUMED else None,
        released_at=NOW if status == StockReservation.Status.RELEASED else None,
    )


def _checkout(group, *, expires_at: datetime = LATER, **kwargs):
    return _row(
        group,
        kind=StockReservation.Kind.CHECKOUT_PENDING_PAYMENT,
        expires_at=expires_at,
        **kwargs,
    )


def test_hold_turns_checkout_rows_into_held_rows() -> None:
    group = uuid4()
    first = _checkout(group)
    second = _checkout(group)

    changed = hold_for_fulfilment(group, now=NOW)

    assert changed == 2
    for row in (first, second):
        row.refresh_from_db()
        assert row.kind == StockReservation.Kind.HELD_FOR_FULFILMENT
        assert row.status == StockReservation.Status.ACTIVE


def test_hold_clears_the_expiry_time() -> None:
    group = uuid4()
    row = _checkout(group)

    hold_for_fulfilment(group, now=NOW)

    row.refresh_from_db()
    assert row.expires_at is None


def test_hold_is_idempotent() -> None:
    group = uuid4()
    row = _checkout(group)

    assert hold_for_fulfilment(group, now=NOW) == 1
    row.refresh_from_db()
    before = row.updated_at

    assert hold_for_fulfilment(group, now=NOW) == 0
    row.refresh_from_db()
    assert row.updated_at == before


def test_hold_refuses_expired_rows() -> None:
    group = uuid4()
    _checkout(group, expires_at=NOW - timedelta(minutes=1))

    with pytest.raises(ReservationExpiredError):
        hold_for_fulfilment(group, now=NOW)


def test_hold_refuses_a_row_that_expires_exactly_now() -> None:
    group = uuid4()
    row = _checkout(group, expires_at=NOW)

    with pytest.raises(ReservationExpiredError):
        hold_for_fulfilment(group, now=NOW)

    row.refresh_from_db()
    assert row.status == StockReservation.Status.ACTIVE
    assert row.kind == StockReservation.Kind.CHECKOUT_PENDING_PAYMENT
    assert row.expires_at == NOW


def test_hold_refuses_a_group_with_released_rows() -> None:
    group = uuid4()
    _checkout(group)
    _checkout(group, status=StockReservation.Status.RELEASED)

    with pytest.raises(ReservationStateError):
        hold_for_fulfilment(group, now=NOW)


def test_hold_refuses_an_unknown_group() -> None:
    with pytest.raises(ReservationNotFoundError):
        hold_for_fulfilment(uuid4(), now=NOW)


def test_hold_moves_updated_at() -> None:
    group = uuid4()
    row = _checkout(group)
    before = row.updated_at

    hold_for_fulfilment(group, now=NOW)

    row.refresh_from_db()
    assert row.updated_at > before


def test_release_group_releases_active_rows() -> None:
    group = uuid4()
    first = _row(group)
    second = _row(group)

    assert release_group(group, now=NOW) == 2

    for row in (first, second):
        row.refresh_from_db()
        assert row.status == StockReservation.Status.RELEASED


def test_release_sets_released_at() -> None:
    group = uuid4()
    row = _row(group)

    release_group(group, now=NOW)

    row.refresh_from_db()
    assert row.released_at == NOW


def test_release_is_idempotent() -> None:
    group = uuid4()
    row = _row(group)
    assert release_group(group, now=NOW) == 1
    row.refresh_from_db()
    released_at = row.released_at

    assert release_group(group, now=NOW) == 0
    row.refresh_from_db()
    assert row.released_at == released_at


def test_release_leaves_consumed_rows_alone() -> None:
    group = uuid4()
    consumed = _row(group, status=StockReservation.Status.CONSUMED)
    _row(group)

    assert release_group(group, now=NOW) == 1

    consumed.refresh_from_db()
    assert consumed.status == StockReservation.Status.CONSUMED
    assert consumed.consumed_at == NOW
    assert consumed.released_at is None


def test_release_returns_the_number_of_rows_released() -> None:
    group = uuid4()
    _row(group)
    _row(group)
    _row(group)

    assert release_group(group, now=NOW) == 3


def test_release_makes_the_stock_available_again() -> None:
    variant = _variant(stock="5.000")
    group = uuid4()
    _row(group, variant=variant, quantity="3.000")

    assert get_available_quantity(variant) == Decimal("2.000")

    release_group(group, now=NOW)

    assert get_available_quantity(variant) == Decimal("5.000")


def test_release_of_an_unknown_group_returns_zero() -> None:
    assert release_group(uuid4(), now=NOW) == 0


def test_consume_marks_a_held_row_consumed() -> None:
    row = _row(uuid4())

    consume_reservation(row.pk, now=NOW)

    row.refresh_from_db()
    assert row.status == StockReservation.Status.CONSUMED


def test_consume_sets_consumed_at() -> None:
    row = _row(uuid4())

    consume_reservation(row.pk, now=NOW)

    row.refresh_from_db()
    assert row.consumed_at == NOW


def test_consume_refuses_a_checkout_pending_row() -> None:
    row = _checkout(uuid4())

    with pytest.raises(ReservationStateError):
        consume_reservation(row.pk, now=NOW)


def test_consume_refuses_a_released_row() -> None:
    row = _row(uuid4(), status=StockReservation.Status.RELEASED)

    with pytest.raises(ReservationStateError):
        consume_reservation(row.pk, now=NOW)


def test_consume_refuses_a_row_that_is_already_consumed() -> None:
    row = _row(uuid4(), status=StockReservation.Status.CONSUMED)

    with pytest.raises(ReservationStateError):
        consume_reservation(row.pk, now=NOW)


def test_consume_does_not_change_on_hand() -> None:
    variant = _variant(stock="5.000")
    row = _row(uuid4(), variant=variant, quantity="2.000")
    movements_before = InventoryMovement.objects.count()

    consume_reservation(row.pk, now=NOW)

    assert get_on_hand(variant) == Decimal("5.000")
    assert InventoryMovement.objects.count() == movements_before


def test_expire_releases_expired_checkout_rows() -> None:
    group = uuid4()
    row = _checkout(group, expires_at=NOW - timedelta(minutes=1))

    assert expire_stale_reservations(now=NOW) == 1

    row.refresh_from_db()
    assert row.status == StockReservation.Status.RELEASED
    assert row.released_at == NOW


def test_expire_releases_a_row_that_expires_exactly_now() -> None:
    group = uuid4()
    row = _checkout(group, expires_at=NOW)

    assert expire_stale_reservations(now=NOW) == 1

    row.refresh_from_db()
    assert row.status == StockReservation.Status.RELEASED
    assert row.released_at == NOW


def test_expire_leaves_unexpired_checkout_rows() -> None:
    group = uuid4()
    row = _checkout(group, expires_at=LATER)

    assert expire_stale_reservations(now=NOW) == 0

    row.refresh_from_db()
    assert row.status == StockReservation.Status.ACTIVE


def test_expire_leaves_held_rows() -> None:
    row = _row(uuid4())

    assert expire_stale_reservations(now=NOW) == 0

    row.refresh_from_db()
    assert row.status == StockReservation.Status.ACTIVE


def test_expire_returns_the_number_released() -> None:
    _checkout(uuid4(), expires_at=NOW - timedelta(minutes=1))
    _checkout(uuid4(), expires_at=NOW - timedelta(minutes=2))

    assert expire_stale_reservations(now=NOW) == 2


def test_expire_is_idempotent() -> None:
    _checkout(uuid4(), expires_at=NOW - timedelta(minutes=1))
    assert expire_stale_reservations(now=NOW) == 1

    assert expire_stale_reservations(now=NOW) == 0


def test_expire_makes_the_stock_available_again() -> None:
    variant = _variant(stock="5.000")
    group = uuid4()
    _checkout(
        group,
        expires_at=NOW + timedelta(minutes=10),
        variant=variant,
        quantity="3.000",
    )

    assert get_available_quantity(variant, now=NOW) == Decimal("2.000")

    assert expire_stale_reservations(now=NOW + timedelta(minutes=15)) == 1

    assert get_available_quantity(variant, now=NOW) == Decimal("5.000")


def test_release_moves_updated_at() -> None:
    row = _row(uuid4())
    before = row.updated_at

    release_group(row.checkout_group, now=NOW)

    row.refresh_from_db()
    assert row.updated_at > before


def test_consume_moves_updated_at() -> None:
    row = _row(uuid4())
    before = row.updated_at

    consume_reservation(row.pk, now=NOW)

    row.refresh_from_db()
    assert row.updated_at > before


def test_expire_moves_updated_at() -> None:
    row = _checkout(uuid4(), expires_at=NOW - timedelta(minutes=1))
    before = row.updated_at

    expire_stale_reservations(now=NOW)

    row.refresh_from_db()
    assert row.updated_at > before


def test_release_only_touches_the_given_group() -> None:
    variant = _variant(stock="5.000")
    released_group = uuid4()
    other_group = uuid4()
    released = _row(released_group, variant=variant, quantity="2.000")
    other = _row(other_group, variant=variant, quantity="1.000")

    assert release_group(released_group, now=NOW) == 1

    released.refresh_from_db()
    other.refresh_from_db()
    assert released.status == StockReservation.Status.RELEASED
    assert other.status == StockReservation.Status.ACTIVE
    assert get_available_quantity(variant, now=NOW) == Decimal("4.000")


def test_hold_refuses_a_group_with_a_consumed_row() -> None:
    group = uuid4()
    active = _checkout(group)
    consumed = _checkout(group, status=StockReservation.Status.CONSUMED)

    with pytest.raises(ReservationStateError):
        hold_for_fulfilment(group, now=NOW)

    active.refresh_from_db()
    consumed.refresh_from_db()
    assert active.status == StockReservation.Status.ACTIVE
    assert active.kind == StockReservation.Kind.CHECKOUT_PENDING_PAYMENT
    assert consumed.status == StockReservation.Status.CONSUMED
