"""Concurrency tests for stock reservations (Slice 34, D63).

Each test starts real threads. Every thread gets its own database connection,
so the data is created and committed in the main thread before the threads
start, and every thread closes its own connection in a ``finally`` block.
Assertions are on counts and sums, never on timing or order.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache
from django.db import connection, transaction

from apps.accounts.models import User
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.core.models import ShopSettings
from apps.inventory.models import StockReservation
from apps.inventory.reservations import (
    ReservationGroupExistsError,
    ReservationLine,
    ReservationShortfallError,
    ReservationStateError,
    hold_for_fulfilment,
    release_group,
    reserve_stock,
)
from apps.inventory.tests.factories import (
    InventoryMovementFactory,
    StockReservationFactory,
)
from apps.inventory.tests.test_services_concurrency import _run_concurrently
from tests.factories import UserFactory

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=IST)
LATER = NOW + timedelta(minutes=10)


@pytest.fixture(autouse=True)
def _settings_row():
    """Create the settings row and empty the cache before any thread runs."""
    cache.clear()
    ShopSettings.load()
    yield
    cache.clear()


def _variant(*, stock: str = "5.000", loose: bool = False) -> ProductVariant:
    variant = ProductVariantFactory(
        is_sold_loose=loose,
        unit=ProductVariant.Unit.KG if loose else ProductVariant.Unit.PACKET,
        pack_size=Decimal("1.000"),
        quantity_step=Decimal("0.001") if loose else Decimal("1"),
    )
    InventoryMovementFactory(variant=variant, quantity_change=Decimal(stock))
    return variant


def _checkout_row(group, variant: ProductVariant, quantity: str = "1.000"):
    return StockReservationFactory(
        checkout_group=group,
        variant=variant,
        quantity=Decimal(quantity),
        kind=StockReservation.Kind.CHECKOUT_PENDING_PAYMENT,
        expires_at=LATER,
    )


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_reserves_of_the_last_unit_allow_only_one() -> None:
    variant = _variant(stock="1.000")
    user = UserFactory()
    user_pk = user.pk
    variant_pk = variant.pk

    def worker(index):
        thread_user = User.objects.get(pk=user_pk)
        return reserve_stock(
            user=thread_user,
            lines=[ReservationLine(variant_id=variant_pk, quantity=Decimal("1"))],
            kind=StockReservation.Kind.HELD_FOR_FULFILMENT,
            now=NOW,
        )

    outcomes = _run_concurrently(worker, count=2)

    ok = [outcome for outcome in outcomes if outcome[0] == "ok"]
    errors = [outcome for outcome in outcomes if outcome[0] == "error"]
    assert len(ok) == 1
    assert len(errors) == 1
    assert isinstance(errors[0][1], ReservationShortfallError)
    assert StockReservation.objects.filter(variant_id=variant_pk).count() == 1


@pytest.mark.django_db(transaction=True)
def test_ten_simultaneous_reserves_never_exceed_the_stock() -> None:
    variant = _variant(stock="5.000")
    user = UserFactory()
    user_pk = user.pk
    variant_pk = variant.pk

    def worker(index):
        thread_user = User.objects.get(pk=user_pk)
        return reserve_stock(
            user=thread_user,
            lines=[ReservationLine(variant_id=variant_pk, quantity=Decimal("1"))],
            kind=StockReservation.Kind.HELD_FOR_FULFILMENT,
            now=NOW,
        )

    outcomes = _run_concurrently(worker, count=10)

    ok = [outcome for outcome in outcomes if outcome[0] == "ok"]
    errors = [outcome for outcome in outcomes if outcome[0] == "error"]
    assert len(ok) == 5
    assert len(errors) == 5
    assert all(isinstance(outcome[1], ReservationShortfallError) for outcome in errors)
    assert (
        StockReservation.objects.filter(
            variant_id=variant_pk,
            status=StockReservation.Status.ACTIVE,
        ).count()
        == 5
    )


@pytest.mark.django_db(transaction=True)
def test_loose_reserves_in_thousandths_never_exceed_the_stock() -> None:
    # 6 x 0.400 = 2.400 is more than the 1.000 in stock, so only 2 get through.
    variant = _variant(stock="1.000", loose=True)
    user = UserFactory()
    user_pk = user.pk
    variant_pk = variant.pk

    def worker(index):
        thread_user = User.objects.get(pk=user_pk)
        return reserve_stock(
            user=thread_user,
            lines=[ReservationLine(variant_id=variant_pk, quantity=Decimal("0.400"))],
            kind=StockReservation.Kind.HELD_FOR_FULFILMENT,
            now=NOW,
        )

    outcomes = _run_concurrently(worker, count=6)

    ok = [outcome for outcome in outcomes if outcome[0] == "ok"]
    errors = [outcome for outcome in outcomes if outcome[0] == "error"]
    assert len(ok) == 2
    assert len(errors) == 4
    assert all(isinstance(outcome[1], ReservationShortfallError) for outcome in errors)

    reserved = sum(row.quantity for row in StockReservation.objects.filter(variant_id=variant_pk))
    assert reserved == Decimal("0.800")


@pytest.mark.django_db(transaction=True)
def test_reserves_with_lines_in_opposite_order_do_not_deadlock() -> None:
    first = _variant(stock="5.000")
    second = _variant(stock="5.000")
    user = UserFactory()
    user_pk = user.pk
    first_pk = first.pk
    second_pk = second.pk

    def worker(index):
        thread_user = User.objects.get(pk=user_pk)
        order = [first_pk, second_pk] if index == 0 else [second_pk, first_pk]
        return reserve_stock(
            user=thread_user,
            lines=[
                ReservationLine(variant_id=variant_id, quantity=Decimal("1"))
                for variant_id in order
            ],
            kind=StockReservation.Kind.HELD_FOR_FULFILMENT,
            now=NOW,
        )

    outcomes = _run_concurrently(worker, count=2)

    assert all(outcome[0] == "ok" for outcome in outcomes)
    assert StockReservation.objects.count() == 4


@pytest.mark.django_db(transaction=True)
def test_a_reserve_waits_for_a_lock_held_on_the_variant() -> None:
    variant = _variant(stock="1.000")
    holder = UserFactory()
    reserver = UserFactory()
    variant_pk = variant.pk
    holder_pk = holder.pk
    reserver_pk = reserver.pk
    group = uuid4()

    locked = threading.Event()
    errors: list[BaseException] = []
    shortfalls: list[BaseException] = []

    def hold_the_lock() -> None:
        try:
            with transaction.atomic():
                ProductVariant.objects.select_for_update().get(pk=variant_pk)
                StockReservation.objects.create(
                    variant_id=variant_pk,
                    user_id=holder_pk,
                    quantity=Decimal("1.000"),
                    kind=StockReservation.Kind.HELD_FOR_FULFILMENT,
                    checkout_group=group,
                )
                locked.set()
                time.sleep(0.5)
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    def reserve_while_locked() -> None:
        try:
            assert locked.wait(timeout=10)
            thread_user = User.objects.get(pk=reserver_pk)
            reserve_stock(
                user=thread_user,
                lines=[ReservationLine(variant_id=variant_pk, quantity=Decimal("1"))],
                kind=StockReservation.Kind.HELD_FOR_FULFILMENT,
                now=NOW,
            )
        except ReservationShortfallError as exc:
            shortfalls.append(exc)
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    holder_thread = threading.Thread(target=hold_the_lock)
    reserver_thread = threading.Thread(target=reserve_while_locked)
    holder_thread.start()
    reserver_thread.start()
    holder_thread.join(timeout=30)
    reserver_thread.join(timeout=30)

    assert not errors, errors
    assert len(shortfalls) == 1
    # Without the variant lock the reserver would read the old availability.
    assert StockReservation.objects.filter(variant_id=variant_pk).count() == 1


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_holds_of_one_group_both_succeed_once() -> None:
    first = _variant(stock="10.000")
    second = _variant(stock="10.000")
    group = uuid4()
    _checkout_row(group, first)
    _checkout_row(group, second)

    def worker(index):
        return hold_for_fulfilment(group, now=NOW)

    outcomes = _run_concurrently(worker, count=2)

    assert all(outcome[0] == "ok" for outcome in outcomes)
    assert sum(outcome[1] for outcome in outcomes) == 2
    assert (
        StockReservation.objects.filter(
            checkout_group=group,
            kind=StockReservation.Kind.HELD_FOR_FULFILMENT,
        ).count()
        == 2
    )


@pytest.mark.django_db(transaction=True)
def test_hold_and_release_racing_end_in_one_consistent_state() -> None:
    first = _variant(stock="10.000")
    second = _variant(stock="10.000")
    group = uuid4()
    _checkout_row(group, first)
    _checkout_row(group, second)

    def worker(index):
        if index == 0:
            return hold_for_fulfilment(group, now=NOW)
        return release_group(group, now=NOW)

    outcomes = _run_concurrently(worker, count=2)

    errors = [outcome[1] for outcome in outcomes if outcome[0] == "error"]
    assert all(isinstance(error, ReservationStateError) for error in errors)

    rows = list(StockReservation.objects.filter(checkout_group=group))
    statuses = {row.status for row in rows}
    assert len(statuses) == 1
    if statuses == {StockReservation.Status.ACTIVE}:
        assert {row.kind for row in rows} == {StockReservation.Kind.HELD_FOR_FULFILMENT}
    else:
        assert statuses == {StockReservation.Status.RELEASED}


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_reserves_with_the_same_group_allow_only_one() -> None:
    variant = _variant(stock="10.000")
    user = UserFactory()
    user_pk = user.pk
    variant_pk = variant.pk
    group = uuid4()

    def worker(index):
        thread_user = User.objects.get(pk=user_pk)
        return reserve_stock(
            user=thread_user,
            lines=[ReservationLine(variant_id=variant_pk, quantity=Decimal("1"))],
            kind=StockReservation.Kind.HELD_FOR_FULFILMENT,
            checkout_group=group,
            now=NOW,
        )

    outcomes = _run_concurrently(worker, count=2)

    ok = [outcome for outcome in outcomes if outcome[0] == "ok"]
    errors = [outcome for outcome in outcomes if outcome[0] == "error"]
    assert len(ok) == 1
    assert len(errors) == 1
    assert isinstance(errors[0][1], ReservationGroupExistsError)
    assert StockReservation.objects.filter(checkout_group=group).count() == 1
