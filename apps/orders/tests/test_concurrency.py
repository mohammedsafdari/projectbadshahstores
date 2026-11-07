"""Concurrency tests for order placement, transitions and cancellation (D64).

Every test starts real threads. The data is created and committed first, each
thread gets its own database connection and closes it in a ``finally`` block,
and the assertions are on final counts, statuses and log chains, never on
timing or on which thread won.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache
from django.db import connection, transaction

from apps.accounts.models import Address, User
from apps.accounts.services import create_address
from apps.cart.models import CartItem
from apps.cart.services import add_item, build_cart
from apps.cart.stores import DbCartStore
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.core.models import ShopSettings
from apps.delivery.tests.factories import DeliveryZoneFactory, PinCodeZoneFactory
from apps.inventory.models import StockReservation
from apps.inventory.reservations import ReservationShortfallError
from apps.inventory.services import receive_stock
from apps.inventory.tests.test_services_concurrency import _run_concurrently
from apps.orders.models import Order, OrderStatusLog
from apps.orders.services import (
    InvalidTransitionError,
    OrderError,
    cancel_order_by_customer,
    place_order,
    transition_order,
)
from apps.orders.state import CANCELLED, CONFIRMED, PACKING
from apps.orders.tests.factories import OrderFactory
from tests.factories import UserFactory

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=IST)


@pytest.fixture(autouse=True)
def _settings_row():
    """Create the settings row and empty the cache before any thread runs."""
    cache.clear()
    ShopSettings.load()
    yield
    cache.clear()


def _serve(pincode: str = "452012") -> None:
    zone = DeliveryZoneFactory()
    PinCodeZoneFactory(zone=zone, pincode=pincode)


def _address(user: User, pincode: str = "452012") -> Address:
    return create_address(
        user,
        recipient_name="Asha Sharma",
        recipient_phone="+919876543210",
        line1="12 Test Road",
        city="Indore",
        state="Madhya Pradesh",
        pincode=pincode,
        label="HOME",
    )


def _variant(
    *,
    stock: str = "10.000",
    price: str = "100.00",
    mrp: str = "120.00",
) -> ProductVariant:
    variant = ProductVariantFactory(
        is_sold_loose=False,
        unit=ProductVariant.Unit.PACKET,
        pack_size=Decimal("1.000"),
        quantity_step=Decimal("1"),
        selling_price=Decimal(price),
        mrp=Decimal(mrp),
    )
    receive_stock(variant=variant, quantity=Decimal(stock))
    return variant


def _fill(user: User, variant: ProductVariant, quantity: str = "2") -> None:
    add_item(DbCartStore(user), variant, Decimal(quantity))


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_orders_for_the_last_item_create_one_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One unit at 200 meets the 200 minimum order value, so the stale summary
    # is checkout-ready even though only one unit exists.
    variant = _variant(stock="1.000", price="200.00", mrp="240.00")
    _serve()
    first = UserFactory()
    second = UserFactory()
    first_address = _address(first)
    second_address = _address(second)
    _fill(first, variant, "1")
    _fill(second, variant, "1")

    # Both summaries are built while the unit still exists, so both checkouts
    # pass the cart check and meet in reserve_stock; only one can reserve it.
    summaries = {
        first.pk: build_cart(DbCartStore(first)),
        second.pk: build_cart(DbCartStore(second)),
    }
    groups = {first.pk: uuid4(), second.pk: uuid4()}
    monkeypatch.setattr(
        "apps.orders.services.build_cart",
        lambda store: summaries[store.user.pk],
    )

    first_pk = first.pk
    second_pk = second.pk
    first_address_pk = first_address.pk
    second_address_pk = second_address.pk

    def worker(index: int):
        user_pk = first_pk if index == 0 else second_pk
        address_pk = first_address_pk if index == 0 else second_address_pk
        thread_user = User.objects.get(pk=user_pk)
        return place_order(
            user=thread_user,
            address_id=address_pk,
            payment_method=Order.PaymentMethod.COD,
            checkout_group=groups[user_pk],
            now=NOW,
        )

    outcomes = _run_concurrently(worker, count=2)

    ok = [outcome for outcome in outcomes if outcome[0] == "ok"]
    errors = [outcome for outcome in outcomes if outcome[0] == "error"]
    assert len(ok) == 1
    assert len(errors) == 1
    assert isinstance(errors[0][1], ReservationShortfallError)
    assert Order.objects.count() == 1
    assert StockReservation.objects.count() == 1
    # The winner's cart is empty; the loser's line is still there.
    assert CartItem.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_submissions_of_one_group_create_one_order() -> None:
    variant = _variant()
    _serve()
    user = UserFactory()
    address = _address(user)
    _fill(user, variant)
    group = uuid4()
    user_pk = user.pk
    address_pk = address.pk

    def worker(index: int):
        thread_user = User.objects.get(pk=user_pk)
        return place_order(
            user=thread_user,
            address_id=address_pk,
            payment_method=Order.PaymentMethod.COD,
            checkout_group=group,
            now=NOW,
        )

    outcomes = _run_concurrently(worker, count=2)

    assert all(outcome[0] == "ok" for outcome in outcomes)
    created = sorted(outcome[1].created for outcome in outcomes)
    assert created == [False, True]
    assert Order.objects.count() == 1
    assert OrderStatusLog.objects.count() == 2
    assert StockReservation.objects.count() == 1
    assert CartItem.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_place_order_waits_for_a_lock_held_on_the_user_row() -> None:
    user = UserFactory()
    group = uuid4()
    user_pk = user.pk
    locked = threading.Event()
    errors: list[BaseException] = []
    results: list[object] = []

    def hold_the_user() -> None:
        try:
            with transaction.atomic():
                holder = User.objects.select_for_update().get(pk=user_pk)
                OrderFactory(user=holder, checkout_group=group)
                locked.set()
                time.sleep(0.5)
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    def place_while_locked() -> None:
        try:
            assert locked.wait(timeout=10)
            thread_user = User.objects.get(pk=user_pk)
            results.append(
                place_order(
                    user=thread_user,
                    address_id=999999,
                    payment_method=Order.PaymentMethod.COD,
                    checkout_group=group,
                    now=NOW,
                )
            )
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    holder_thread = threading.Thread(target=hold_the_user)
    placer_thread = threading.Thread(target=place_while_locked)
    holder_thread.start()
    placer_thread.start()
    holder_thread.join(timeout=30)
    placer_thread.join(timeout=30)

    assert not errors, errors
    assert len(results) == 1
    assert results[0].created is False
    assert Order.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_transition_order_waits_for_a_lock_held_on_the_order_row() -> None:
    user = UserFactory()
    staff = UserFactory()
    order = OrderFactory(user=user, status=CONFIRMED)
    order_pk = order.pk
    user_pk = user.pk
    staff_pk = staff.pk
    locked = threading.Event()
    errors: list[BaseException] = []
    results: list[str] = []

    def hold_the_order() -> None:
        try:
            with transaction.atomic():
                locked_order = Order.objects.select_for_update().get(pk=order_pk)
                locked_order.status = CANCELLED
                locked_order.cancel_reason = "Holder"
                locked_order.cancelled_at = NOW
                locked_order.save(
                    update_fields=["status", "cancel_reason", "cancelled_at", "updated_at"]
                )
                OrderStatusLog.objects.create(
                    order_id=order_pk,
                    from_status=CONFIRMED,
                    to_status=CANCELLED,
                    actor_id=user_pk,
                    note="Holder",
                )
                locked.set()
                time.sleep(0.5)
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    def transition_while_locked() -> None:
        try:
            assert locked.wait(timeout=10)
            actor = User.objects.get(pk=staff_pk)
            try:
                transition_order(order_pk, PACKING, actor=actor, now=NOW)
            except InvalidTransitionError:
                results.append("refused")
            else:
                results.append("moved")
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    holder_thread = threading.Thread(target=hold_the_order)
    mover_thread = threading.Thread(target=transition_while_locked)
    holder_thread.start()
    mover_thread.start()
    holder_thread.join(timeout=30)
    mover_thread.join(timeout=30)

    assert not errors, errors
    assert results == ["refused"]
    final = Order.objects.get(pk=order_pk)
    assert final.status == CANCELLED


@pytest.mark.django_db(transaction=True)
def test_cancel_and_packing_racing_end_in_one_consistent_state() -> None:
    variant = _variant()
    _serve()
    user = UserFactory()
    address = _address(user)
    _fill(user, variant)
    placed = place_order(
        user=user,
        address_id=address.pk,
        payment_method=Order.PaymentMethod.COD,
        checkout_group=uuid4(),
        now=NOW,
    )
    order_pk = placed.order.pk
    user_pk = user.pk
    staff = UserFactory()
    staff_pk = staff.pk
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []
    outcomes: list[tuple[str, object]] = []

    def worker(index: int) -> None:
        try:
            barrier.wait(timeout=10)
            if index == 0:
                thread_user = User.objects.get(pk=user_pk)
                try:
                    outcomes.append(
                        ("cancel", cancel_order_by_customer(thread_user, order_pk, now=NOW))
                    )
                except OrderError as exc:
                    outcomes.append(("refused", exc))
            else:
                actor = User.objects.get(pk=staff_pk)
                try:
                    outcomes.append(("pack", transition_order(order_pk, PACKING, actor=actor)))
                except OrderError as exc:
                    outcomes.append(("refused", exc))
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(outcomes) == 2
    winners = [outcome for outcome in outcomes if outcome[0] in ("cancel", "pack")]
    refusals = [outcome for outcome in outcomes if outcome[0] == "refused"]
    assert len(winners) == 1
    assert len(refusals) == 1

    final = Order.objects.get(pk=order_pk)
    assert final.status in {PACKING, CANCELLED}
    logs = list(OrderStatusLog.objects.filter(order_id=order_pk).order_by("created_at", "pk"))
    assert len(logs) >= 3
    assert logs[0].from_status == ""
    assert logs[0].to_status == "PLACED"
    assert logs[1].from_status == "PLACED"
    assert logs[1].to_status == CONFIRMED

    # Every row after CONFIRMED must follow the previous row without a gap.
    previous = CONFIRMED
    for log in logs[2:]:
        assert log.from_status == previous
        previous = log.to_status
    assert previous == final.status

    holds = StockReservation.objects.filter(checkout_group=final.checkout_group)
    assert holds.exists()
    if final.status == CANCELLED:
        assert all(hold.status == StockReservation.Status.RELEASED for hold in holds)
    else:
        assert all(hold.status == StockReservation.Status.ACTIVE for hold in holds)
