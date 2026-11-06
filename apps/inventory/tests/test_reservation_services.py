"""Tests for reserve_stock (Slice 34, D63)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache

from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.core.models import ShopSettings
from apps.inventory.models import StockReservation
from apps.inventory.reservations import (
    ReservationError,
    ReservationGroupExistsError,
    ReservationLine,
    ReservationShortfallError,
    VariantNotReservableError,
    reserve_stock,
)
from apps.inventory.services import InvalidQuantityError
from apps.inventory.tests.factories import (
    InventoryMovementFactory,
    StockReservationFactory,
)
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=IST)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """The locmem cache outlives a test's database transaction, so empty it."""
    cache.clear()
    yield
    cache.clear()


def _variant(*, stock: str = "10.000", loose: bool = False) -> ProductVariant:
    variant = ProductVariantFactory(
        is_sold_loose=loose,
        unit=ProductVariant.Unit.KG if loose else ProductVariant.Unit.PACKET,
        pack_size=Decimal("1.000"),
        quantity_step=Decimal("0.001") if loose else Decimal("1"),
    )
    if Decimal(stock):
        InventoryMovementFactory(variant=variant, quantity_change=Decimal(stock))
    return variant


def _line(variant: ProductVariant, quantity: str) -> ReservationLine:
    return ReservationLine(variant_id=variant.pk, quantity=Decimal(quantity))


def _reserve(
    user,
    lines,
    *,
    kind: str = StockReservation.Kind.HELD_FOR_FULFILMENT,
    group: UUID | None = None,
    now: datetime | None = NOW,
):
    return reserve_stock(
        user=user,
        lines=lines,
        kind=kind,
        checkout_group=group,
        now=now,
    )


def _set_reservation_minutes(minutes: int) -> None:
    row = ShopSettings.load()
    row.reservation_minutes = minutes
    row.save(update_fields=["reservation_minutes", "updated_at"])


def _set_buffer(value: int) -> None:
    row = ShopSettings.load()
    row.online_stock_buffer = value
    row.save(update_fields=["online_stock_buffer", "updated_at"])


def test_reserve_creates_one_row_per_line() -> None:
    user = UserFactory()
    first = _variant(stock="5.000")
    second = _variant(stock="5.000")

    rows = _reserve(user, [_line(first, "2"), _line(second, "3")])

    assert len(rows) == 2
    assert StockReservation.objects.filter(user=user).count() == 2
    by_variant = {row.variant_id: row.quantity for row in rows}
    assert by_variant == {first.pk: Decimal("2.000"), second.pk: Decimal("3.000")}


def test_reserve_returns_the_rows_with_one_shared_group() -> None:
    user = UserFactory()
    first = _variant(stock="5.000")
    second = _variant(stock="5.000")

    rows = _reserve(user, [_line(first, "1"), _line(second, "1")])

    assert len({row.checkout_group for row in rows}) == 1


def test_reserve_uses_the_given_group() -> None:
    user = UserFactory()
    variant = _variant()
    group = uuid4()

    rows = _reserve(user, [_line(variant, "1")], group=group)

    assert [row.checkout_group for row in rows] == [group]


def test_reserve_generates_a_group_when_none_is_given() -> None:
    user = UserFactory()
    variant = _variant()

    rows = _reserve(user, [_line(variant, "1")])

    assert isinstance(rows[0].checkout_group, UUID)


def test_checkout_kind_gets_an_expiry_from_reservation_minutes() -> None:
    user = UserFactory()
    variant = _variant()

    rows = _reserve(
        user,
        [_line(variant, "1")],
        kind=StockReservation.Kind.CHECKOUT_PENDING_PAYMENT,
    )

    assert rows[0].expires_at == NOW + timedelta(minutes=15)


def test_held_kind_has_no_expiry() -> None:
    user = UserFactory()
    variant = _variant()

    rows = _reserve(user, [_line(variant, "1")])

    assert rows[0].expires_at is None


def test_reserve_uses_the_current_reservation_minutes_setting() -> None:
    _set_reservation_minutes(45)
    user = UserFactory()
    variant = _variant()

    rows = _reserve(
        user,
        [_line(variant, "1")],
        kind=StockReservation.Kind.CHECKOUT_PENDING_PAYMENT,
    )

    assert rows[0].expires_at == NOW + timedelta(minutes=45)


def test_reserve_refuses_more_than_available_and_writes_nothing() -> None:
    user = UserFactory()
    variant = _variant(stock="5.000")

    with pytest.raises(ReservationShortfallError):
        _reserve(user, [_line(variant, "6")])

    assert not StockReservation.objects.exists()


def test_reserve_reports_every_short_line_not_just_the_first() -> None:
    user = UserFactory()
    first = _variant(stock="1.000")
    second = _variant(stock="1.000")

    with pytest.raises(ReservationShortfallError) as excinfo:
        _reserve(user, [_line(first, "2"), _line(second, "3")])

    shortfalls = excinfo.value.shortfalls
    assert len(shortfalls) == 2
    assert {item.variant_id for item in shortfalls} == {first.pk, second.pk}


def test_the_shortfall_carries_requested_and_available_amounts() -> None:
    user = UserFactory()
    variant = _variant(stock="5.000")

    with pytest.raises(ReservationShortfallError) as excinfo:
        _reserve(user, [_line(variant, "6")])

    shortfall = excinfo.value.shortfalls[0]
    assert shortfall.requested == Decimal("6.000")
    assert shortfall.available == Decimal("5.000")


def test_reserve_counts_the_online_stock_buffer() -> None:
    _set_buffer(2)
    user = UserFactory()
    variant = _variant(stock="5.000")

    with pytest.raises(ReservationShortfallError) as excinfo:
        _reserve(user, [_line(variant, "4")])

    assert excinfo.value.shortfalls[0].available == Decimal("3.000")


def test_reserve_counts_existing_holds() -> None:
    user = UserFactory()
    variant = _variant(stock="5.000")
    StockReservationFactory(variant=variant, quantity=Decimal("2.000"))

    with pytest.raises(ReservationShortfallError) as excinfo:
        _reserve(user, [_line(variant, "4")])

    assert excinfo.value.shortfalls[0].available == Decimal("3.000")


def test_reserve_may_take_exactly_the_available_amount() -> None:
    user = UserFactory()
    variant = _variant(stock="5.000")

    rows = _reserve(user, [_line(variant, "5")])

    assert len(rows) == 1
    assert rows[0].quantity == Decimal("5.000")


def test_reserve_refuses_one_thousandth_over_the_available_amount() -> None:
    user = UserFactory()
    variant = _variant(stock="1.000", loose=True)

    with pytest.raises(ReservationShortfallError):
        _reserve(user, [_line(variant, "1.001")])


def test_reserve_refuses_an_empty_line_list() -> None:
    user = UserFactory()

    with pytest.raises(ReservationError):
        _reserve(user, [])


def test_reserve_refuses_a_duplicate_variant_in_the_lines() -> None:
    user = UserFactory()
    variant = _variant()

    with pytest.raises(ReservationError):
        _reserve(user, [_line(variant, "1"), _line(variant, "2")])


def test_reserve_refuses_a_zero_or_negative_quantity() -> None:
    user = UserFactory()
    variant = _variant()

    with pytest.raises(ReservationError):
        _reserve(user, [_line(variant, "0")])


def test_reserve_refuses_a_quantity_the_variant_type_does_not_allow() -> None:
    user = UserFactory()
    variant = _variant(loose=False)

    with pytest.raises(InvalidQuantityError):
        _reserve(user, [_line(variant, "1.500")])


def test_reserve_refuses_float_and_string_quantities() -> None:
    user = UserFactory()
    variant = _variant()
    float_line = ReservationLine(
        variant_id=variant.pk,
        quantity=1.5,  # type: ignore[arg-type]
    )
    string_line = ReservationLine(
        variant_id=variant.pk,
        quantity="1",  # type: ignore[arg-type]
    )

    with pytest.raises(InvalidQuantityError):
        _reserve(user, [float_line])
    with pytest.raises(InvalidQuantityError):
        _reserve(user, [string_line])


def test_reserve_refuses_an_archived_variant() -> None:
    user = UserFactory()
    variant = _variant()
    variant.is_active = False
    variant.save(update_fields=["is_active", "updated_at"])

    with pytest.raises(VariantNotReservableError):
        _reserve(user, [_line(variant, "1")])


def test_reserve_refuses_a_group_that_already_has_reservations() -> None:
    user = UserFactory()
    variant = _variant()
    group = uuid4()
    StockReservationFactory(checkout_group=group)

    with pytest.raises(ReservationGroupExistsError):
        _reserve(user, [_line(variant, "1")], group=group)


def test_reserve_refuses_an_unknown_variant_id() -> None:
    user = UserFactory()

    with pytest.raises(VariantNotReservableError):
        _reserve(
            user,
            [ReservationLine(variant_id=999999, quantity=Decimal("1"))],
        )


def test_a_refused_reserve_leaves_no_rows_for_any_line() -> None:
    user = UserFactory()
    first = _variant(stock="5.000")
    second = _variant(stock="1.000")

    with pytest.raises(ReservationShortfallError):
        _reserve(user, [_line(first, "2"), _line(second, "2")])

    assert not StockReservation.objects.exists()


def test_reserve_accepts_loose_quantities_in_thousandths() -> None:
    user = UserFactory()
    variant = _variant(stock="2.000", loose=True)

    rows = _reserve(user, [_line(variant, "1.234")])

    assert rows[0].quantity == Decimal("1.234")
    assert rows[0].quantity.as_tuple().exponent == -3
