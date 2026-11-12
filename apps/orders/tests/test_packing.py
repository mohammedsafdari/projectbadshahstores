"""Tests for packing and sending out an order (D79).

The stock is real: the helpers receive stock, take a hold for it and build
an order with one line, so the ledger and the hold always have something to
agree about.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from apps.catalog.models import ProductVariant
from apps.inventory.models import InventoryMovement, StockReservation
from apps.inventory.services import InsufficientStockError, adjust_stock, get_on_hand
from apps.orders.models import Order, OrderItem, OrderStatusLog
from apps.orders.services import CustomerNotVerifiedError, InvalidTransitionError
from apps.orders.staff_services import (
    StockHoldUnavailableError,
    pack_and_send_out,
    start_packing,
)
from apps.orders.tests.factories import OrderFactory, OrderItemFactory
from apps.orders.tests.staff_helpers import (
    NOW,
    expired_hold_order,
    hold_for,
    order_with_hold,
    staff,
    stocked_variant,
)
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _two_line_order(
    with_second_hold: bool,
) -> tuple[Order, ProductVariant, StockReservation]:
    """A packing order with two lines and one or two stock holds."""
    customer = UserFactory()
    first_variant = stocked_variant()
    second_variant = stocked_variant()
    group = uuid4()
    hold_for(first_variant, customer, group=group, quantity="2")
    if with_second_hold:
        hold_for(second_variant, customer, group=group, quantity="3")
    order = OrderFactory(
        user=customer,
        status=Order.Status.PACKING,
        checkout_group=group,
    )
    OrderItemFactory(order=order, variant=first_variant, quantity_ordered=Decimal("2"))
    OrderItemFactory(order=order, variant=second_variant, quantity_ordered=Decimal("3"))
    return order, first_variant, second_variant


def _sale_movements():
    return InventoryMovement.objects.filter(
        movement_type=InventoryMovement.MovementType.SALE,
    )


def test_start_packing_moves_confirmed_to_packing() -> None:
    order = OrderFactory(status=Order.Status.CONFIRMED)

    result = start_packing(order.pk, actor=staff(), now=NOW)

    assert result.status == Order.Status.PACKING
    order.refresh_from_db()
    assert order.status == Order.Status.PACKING


def test_start_packing_refuses_other_statuses() -> None:
    order = OrderFactory(status=Order.Status.OUT_FOR_DELIVERY)

    with pytest.raises(InvalidTransitionError):
        start_packing(order.pk, actor=staff(), now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.OUT_FOR_DELIVERY


def test_start_packing_logs_actor() -> None:
    actor = staff()
    order = OrderFactory(status=Order.Status.CONFIRMED)

    start_packing(order.pk, actor=actor, now=NOW)

    log = OrderStatusLog.objects.get(order=order, to_status=Order.Status.PACKING)
    assert log.actor_id == actor.pk
    assert log.note == "Packing started"


def test_send_out_consumes_every_reservation() -> None:
    order, _first_variant, _second_variant = _two_line_order(with_second_hold=True)

    pack_and_send_out(order.pk, actor=staff(), now=NOW)

    holds = StockReservation.objects.filter(checkout_group=order.checkout_group)
    assert holds.count() == 2
    assert set(holds.values_list("status", flat=True)) == {StockReservation.Status.CONSUMED}


def test_send_out_writes_one_sale_movement_per_line() -> None:
    order, first_variant, second_variant = _two_line_order(with_second_hold=True)

    pack_and_send_out(order.pk, actor=staff(), now=NOW)

    sales = _sale_movements()
    assert sales.count() == 2
    sold = set(sales.values_list("variant_id", flat=True))
    assert sold == {first_variant.pk, second_variant.pk}


def test_send_out_sale_quantity_matches_line() -> None:
    order, row, _hold = order_with_hold(quantity="3")

    pack_and_send_out(order.pk, actor=staff(), now=NOW)

    movement = _sale_movements().get(variant=row)
    assert movement.quantity_change == Decimal("-3")


def test_send_out_reduces_stock_once() -> None:
    order, row, _hold = order_with_hold(quantity="2")

    pack_and_send_out(order.pk, actor=staff(), now=NOW)

    assert get_on_hand(row) == Decimal("8")


def test_send_out_moves_to_out_for_delivery() -> None:
    order, _row, _hold = order_with_hold()
    item = order.items.get()
    before = item.updated_at

    pack_and_send_out(order.pk, actor=staff(), now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.OUT_FOR_DELIVERY
    assert OrderStatusLog.objects.filter(
        order=order,
        to_status=Order.Status.OUT_FOR_DELIVERY,
    ).exists()
    # The line is marked as fulfilled, and its timestamp moves with it.
    item.refresh_from_db()
    assert item.item_status == OrderItem.ItemStatus.FULFILLED
    assert item.updated_at > before


def test_send_out_refused_by_delivery_gate_changes_nothing() -> None:
    customer = UserFactory(phone_verified_at=None, email_verified_at=None)
    order, _row, hold = order_with_hold(customer=customer)

    with pytest.raises(CustomerNotVerifiedError):
        pack_and_send_out(order.pk, actor=staff(), now=NOW)

    order.refresh_from_db()
    hold.refresh_from_db()
    assert order.status == Order.Status.PACKING
    assert hold.status == StockReservation.Status.ACTIVE
    assert not _sale_movements().exists()


def test_send_out_gate_message_is_plain_words() -> None:
    customer = UserFactory(phone_verified_at=None, email_verified_at=None)
    order, _row, _hold = order_with_hold(customer=customer)

    with pytest.raises(CustomerNotVerifiedError) as error:
        pack_and_send_out(order.pk, actor=staff(), now=NOW)

    message = str(error.value)
    assert "phone" in message
    assert "verified" in message


def test_send_out_twice_does_not_double_sale() -> None:
    order, row, _hold = order_with_hold()

    pack_and_send_out(order.pk, actor=staff(), now=NOW)
    with pytest.raises(InvalidTransitionError):
        pack_and_send_out(order.pk, actor=staff(), now=NOW)

    assert _sale_movements().count() == 1
    assert get_on_hand(row) == Decimal("8")


def test_send_out_with_expired_reservation_changes_nothing() -> None:
    order, _row, hold = expired_hold_order()

    with pytest.raises(StockHoldUnavailableError):
        pack_and_send_out(order.pk, actor=staff(), now=NOW)

    order.refresh_from_db()
    hold.refresh_from_db()
    assert order.status == Order.Status.PACKING
    assert hold.status == StockReservation.Status.ACTIVE
    assert not _sale_movements().exists()


def test_send_out_refuses_when_hold_quantity_differs_from_the_order_line() -> None:
    customer = UserFactory()
    row = stocked_variant()
    group = uuid4()
    # The hold is for two, but the order line is for three.
    hold = hold_for(row, customer, group=group, quantity="2")
    order = OrderFactory(
        user=customer,
        status=Order.Status.PACKING,
        checkout_group=group,
    )
    OrderItemFactory(order=order, variant=row, quantity_ordered=Decimal("3"))

    with pytest.raises(StockHoldUnavailableError):
        pack_and_send_out(order.pk, actor=staff(), now=NOW)

    order.refresh_from_db()
    hold.refresh_from_db()
    assert order.status == Order.Status.PACKING
    assert hold.status == StockReservation.Status.ACTIVE
    assert not _sale_movements().exists()
    assert get_on_hand(row) == Decimal("10")


def test_send_out_refuses_when_stock_on_hand_is_short() -> None:
    order, row, hold = order_with_hold(quantity="2")
    # The stock is taken away after the hold was taken, so the shop no longer
    # has what the order needs.
    adjust_stock(
        variant=row,
        quantity_change=Decimal("-9"),
        reason="Sold in the shop",
    )

    with pytest.raises(InsufficientStockError):
        pack_and_send_out(order.pk, actor=staff(), now=NOW)

    order.refresh_from_db()
    hold.refresh_from_db()
    assert order.status == Order.Status.PACKING
    # The hold that was consumed is rolled back with the refused sale.
    assert hold.status == StockReservation.Status.ACTIVE
    assert hold.consumed_at is None
    assert not _sale_movements().exists()


def test_send_out_refuses_a_hold_that_has_expired_with_a_plain_message() -> None:
    # The model only allows an expiry on a checkout hold, so an active hold
    # with an expiry in the past is an expired checkout hold.
    order, _row, hold = expired_hold_order()

    with pytest.raises(StockHoldUnavailableError) as error:
        pack_and_send_out(order.pk, actor=staff(), now=NOW)

    assert "expired" in str(error.value)
    hold.refresh_from_db()
    assert hold.status == StockReservation.Status.ACTIVE


def test_send_out_is_atomic_when_one_line_fails() -> None:
    order, first_variant, _second_variant = _two_line_order(with_second_hold=False)

    with pytest.raises(StockHoldUnavailableError):
        pack_and_send_out(order.pk, actor=staff(), now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.PACKING
    assert not _sale_movements().exists()
    assert get_on_hand(first_variant) == Decimal("10")


def test_send_out_from_confirmed_is_refused() -> None:
    order = OrderFactory(status=Order.Status.CONFIRMED)

    with pytest.raises(InvalidTransitionError):
        pack_and_send_out(order.pk, actor=staff(), now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.CONFIRMED
    assert not _sale_movements().exists()


def test_hold_expiring_exactly_now_is_refused() -> None:
    customer = UserFactory()
    row = stocked_variant()
    group = uuid4()
    # A checkout hold carries the expiry. The rule is "expires at or before
    # now", so a hold that expires at exactly this moment is already too late
    # (D63), the same strict boundary the availability rule uses.
    hold = hold_for(
        row,
        customer,
        group=group,
        quantity="2",
        kind=StockReservation.Kind.CHECKOUT_PENDING_PAYMENT,
        expires_at=NOW,
    )
    order = OrderFactory(
        user=customer,
        status=Order.Status.PACKING,
        checkout_group=group,
    )
    OrderItemFactory(order=order, variant=row, quantity_ordered=Decimal("2"))

    with pytest.raises(StockHoldUnavailableError) as error:
        pack_and_send_out(order.pk, actor=staff(), now=NOW)

    assert "expired" in str(error.value)
    order.refresh_from_db()
    hold.refresh_from_db()
    assert order.status == Order.Status.PACKING
    assert hold.status == StockReservation.Status.ACTIVE
    assert not _sale_movements().exists()


def test_active_unexpired_hold_of_wrong_kind_is_refused() -> None:
    customer = UserFactory()
    row = stocked_variant()
    group = uuid4()
    # This hold is active and has not expired, but it was taken for a checkout
    # that has not paid yet, not for fulfilment. The kind alone must refuse it
    # (D79), even though the quantity matches the order line.
    hold = hold_for(
        row,
        customer,
        group=group,
        quantity="2",
        kind=StockReservation.Kind.CHECKOUT_PENDING_PAYMENT,
        expires_at=NOW + timedelta(minutes=30),
    )
    order = OrderFactory(
        user=customer,
        status=Order.Status.PACKING,
        checkout_group=group,
    )
    OrderItemFactory(order=order, variant=row, quantity_ordered=Decimal("2"))

    with pytest.raises(StockHoldUnavailableError) as error:
        pack_and_send_out(order.pk, actor=staff(), now=NOW)

    assert "not held for this order" in str(error.value)
    order.refresh_from_db()
    hold.refresh_from_db()
    assert order.status == Order.Status.PACKING
    assert hold.status == StockReservation.Status.ACTIVE
    assert hold.consumed_at is None
    assert not _sale_movements().exists()
