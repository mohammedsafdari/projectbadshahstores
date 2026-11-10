"""Tests for the paid and failed order helpers (D77)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import call, patch

import pytest

from apps.orders.models import Order, OrderStatusLog
from apps.orders.services import (
    OrderNotFoundError,
    mark_order_paid,
    mark_order_payment_failed,
)
from apps.orders.tests.factories import OrderFactory
from apps.payments.tests.helpers import NOW, clear_settings, factory_online_order, reset_settings
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before every test."""
    reset_settings()
    yield
    clear_settings()


def test_mark_order_paid_walks_pending_to_paid_to_confirmed() -> None:
    order = factory_online_order()

    result = mark_order_paid(order.pk, now=NOW)

    assert result.status == Order.Status.CONFIRMED
    logs = list(OrderStatusLog.objects.filter(order=order).order_by("pk"))
    assert [log.to_status for log in logs] == [Order.Status.PAID, Order.Status.CONFIRMED]


def test_mark_order_paid_can_start_from_placed() -> None:
    order = factory_online_order(status=Order.Status.PLACED)

    result = mark_order_paid(order.pk, now=NOW)

    assert result.status == Order.Status.CONFIRMED
    logs = list(OrderStatusLog.objects.filter(order=order).order_by("pk"))
    assert [log.to_status for log in logs] == [
        Order.Status.PAYMENT_PENDING,
        Order.Status.PAID,
        Order.Status.CONFIRMED,
    ]


def test_mark_order_paid_can_retry_from_payment_failed() -> None:
    order = factory_online_order(status=Order.Status.PAYMENT_FAILED)

    result = mark_order_paid(order.pk, now=NOW)

    assert result.status == Order.Status.CONFIRMED
    logs = list(OrderStatusLog.objects.filter(order=order).order_by("pk"))
    assert logs[0].to_status == Order.Status.PAYMENT_PENDING


def test_mark_order_paid_is_idempotent_on_a_confirmed_order() -> None:
    order = factory_online_order(status=Order.Status.CONFIRMED)

    result = mark_order_paid(order.pk, now=NOW)

    assert result.status == Order.Status.CONFIRMED
    assert OrderStatusLog.objects.filter(order=order).count() == 0


def test_mark_order_paid_never_revives_a_cancelled_order() -> None:
    order = OrderFactory(
        status=Order.Status.CANCELLED,
        cancelled_at=NOW,
        cancel_reason="Customer called",
        payment_method=Order.PaymentMethod.ONLINE,
        online_amount=Decimal("230.00"),
        amount_due_on_delivery=Decimal("0.00"),
    )

    result = mark_order_paid(order.pk, now=NOW)

    assert result.status == Order.Status.CANCELLED
    assert OrderStatusLog.objects.filter(order=order).count() == 0


def test_mark_order_paid_refuses_an_unknown_order() -> None:
    with pytest.raises(OrderNotFoundError):
        mark_order_paid(999999, now=NOW)


def test_mark_order_paid_moves_updated_at() -> None:
    order = factory_online_order()
    before = order.updated_at

    mark_order_paid(order.pk, now=NOW)

    order.refresh_from_db()
    assert order.updated_at > before


def test_mark_order_paid_sends_the_two_notices_after_commit(
    django_capture_on_commit_callbacks,
) -> None:
    from apps.notifications.messages import EVENT_PLACED, EVENT_STAFF_NEW_ORDER

    order = factory_online_order()

    with (
        patch("apps.orders.services.notify_order_event") as notifier,
        django_capture_on_commit_callbacks(execute=True),
    ):
        mark_order_paid(order.pk, now=NOW)

    assert notifier.call_args_list == [
        call(order.pk, EVENT_PLACED),
        call(order.pk, EVENT_STAFF_NEW_ORDER),
    ]


def test_mark_order_paid_records_the_actor() -> None:
    actor = UserFactory()
    order = factory_online_order()

    mark_order_paid(order.pk, actor=actor, now=NOW)

    logs = OrderStatusLog.objects.filter(order=order).order_by("pk")
    assert [log.actor_id for log in logs] == [actor.pk, actor.pk]


def test_mark_order_payment_failed_moves_pending_to_failed() -> None:
    order = factory_online_order()

    result = mark_order_payment_failed(order.pk, reason="card declined", now=NOW)

    assert result.status == Order.Status.PAYMENT_FAILED
    log = OrderStatusLog.objects.get(order=order)
    assert log.from_status == Order.Status.PAYMENT_PENDING
    assert log.to_status == Order.Status.PAYMENT_FAILED
    assert log.note == "card declined"


def test_mark_order_payment_failed_does_nothing_from_placed() -> None:
    order = factory_online_order(status=Order.Status.PLACED)

    result = mark_order_payment_failed(order.pk, now=NOW)

    assert result.status == Order.Status.PLACED
    assert OrderStatusLog.objects.filter(order=order).count() == 0


def test_mark_order_payment_failed_does_nothing_on_a_confirmed_order() -> None:
    order = factory_online_order(status=Order.Status.CONFIRMED)

    result = mark_order_payment_failed(order.pk, now=NOW)

    assert result.status == Order.Status.CONFIRMED


def test_mark_order_payment_failed_refuses_an_unknown_order() -> None:
    with pytest.raises(OrderNotFoundError):
        mark_order_payment_failed(999999, now=NOW)


def test_mark_order_payment_failed_moves_updated_at() -> None:
    order = factory_online_order()
    before = order.updated_at

    mark_order_payment_failed(order.pk, reason="test", now=NOW)

    order.refresh_from_db()
    assert order.updated_at > before


def test_mark_order_payment_failed_uses_a_default_note() -> None:
    order = factory_online_order()

    mark_order_payment_failed(order.pk, now=NOW)

    assert OrderStatusLog.objects.get(order=order).note == "Online payment failed"
