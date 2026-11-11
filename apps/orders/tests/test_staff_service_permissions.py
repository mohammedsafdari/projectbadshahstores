"""Tests for the capability checks of the staff services (D75, D79).

Every staff action needs the right capability. The role matrix decides:
the owner and a manager have every right, a packer may look at the orders,
pack and send them out, and a delivery person may deliver them.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.accounts.models import User
from apps.orders.models import Order
from apps.orders.staff_queries import list_staff_orders
from apps.orders.staff_services import (
    StaffPermissionError,
    mark_delivered,
    pack_and_send_out,
    staff_cancel_order,
    start_packing,
)
from apps.orders.tests.factories import OrderFactory
from apps.orders.tests.staff_helpers import NOW, order_with_hold, staff

pytestmark = pytest.mark.django_db


def test_list_needs_view_orders() -> None:
    with pytest.raises(StaffPermissionError):
        list_staff_orders(staff(User.ROLE_DELIVERY))

    assert list_staff_orders(staff(User.ROLE_PACKER)) == []


def test_start_packing_needs_start_packing() -> None:
    order = OrderFactory(status=Order.Status.CONFIRMED)

    with pytest.raises(StaffPermissionError):
        start_packing(order.pk, actor=staff(User.ROLE_DELIVERY), now=NOW)

    result = start_packing(order.pk, actor=staff(User.ROLE_PACKER), now=NOW)
    assert result.status == Order.Status.PACKING


def test_send_out_needs_send_out() -> None:
    order, _row, _hold = order_with_hold()

    with pytest.raises(StaffPermissionError):
        pack_and_send_out(order.pk, actor=staff(User.ROLE_DELIVERY), now=NOW)

    result = pack_and_send_out(order.pk, actor=staff(User.ROLE_PACKER), now=NOW)
    assert result.status == Order.Status.OUT_FOR_DELIVERY


def test_deliver_needs_deliver_cod() -> None:
    order = OrderFactory(status=Order.Status.OUT_FOR_DELIVERY)

    with pytest.raises(StaffPermissionError):
        mark_delivered(
            order.pk,
            actor=staff(User.ROLE_PACKER),
            collected_amount=Decimal("230.00"),
            now=NOW,
        )

    result = mark_delivered(
        order.pk,
        actor=staff(User.ROLE_DELIVERY),
        collected_amount=Decimal("230.00"),
        now=NOW,
    )
    assert result.status == Order.Status.DELIVERED


def test_cancel_needs_staff_cancel() -> None:
    order = OrderFactory(status=Order.Status.CONFIRMED)

    with pytest.raises(StaffPermissionError):
        staff_cancel_order(
            order.pk,
            actor=staff(User.ROLE_PACKER),
            reason="Out of stock",
            now=NOW,
        )

    result = staff_cancel_order(
        order.pk,
        actor=staff(User.ROLE_MANAGER),
        reason="Out of stock",
        now=NOW,
    )
    assert result.status == Order.Status.CANCELLED
