"""Tests for the unpaid-online-order expiry job (D77)."""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

import pytest

from apps.cart.models import CartItem
from apps.inventory.models import StockReservation
from apps.notifications.messages import EVENT_PAYMENT_FAILED
from apps.orders.models import Order
from apps.orders.tests.factories import OrderFactory
from apps.payments.services import expire_unpaid_online_orders
from apps.payments.tests.factories import CapturedPaymentFactory
from apps.payments.tests.helpers import NOW, clear_settings, online_order, reset_settings

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row(settings):
    """Create the settings row and enable the fake provider before every test."""
    reset_settings()
    settings.PAYMENT_PROVIDER = "fake"
    yield
    clear_settings()


def _age(order: Order, *, minutes: int = 50, seconds: int = 0) -> None:
    """Move the order's creation time into the past without changing anything else."""
    created = NOW - timedelta(minutes=minutes, seconds=seconds)
    Order.objects.filter(pk=order.pk).update(created_at=created)


def _aged_online_order():
    user, order, row = online_order()
    _age(order)
    return user, order, row


def test_an_aged_unpaid_online_order_is_cancelled() -> None:
    _user, order, _row = _aged_online_order()

    cancelled = expire_unpaid_online_orders(now=NOW)

    order.refresh_from_db()
    assert cancelled == 1
    assert order.status == Order.Status.CANCELLED
    assert order.cancelled_at == NOW
    assert order.cancel_reason == "Online payment was not completed in time."


def test_expiry_releases_the_stock_hold() -> None:
    _user, order, _row = _aged_online_order()

    expire_unpaid_online_orders(now=NOW)

    hold = StockReservation.objects.get(checkout_group=order.checkout_group)
    assert hold.status == StockReservation.Status.RELEASED
    assert hold.released_at == NOW


def test_expiry_puts_the_lines_back_into_the_cart() -> None:
    user, order, row = _aged_online_order()
    assert CartItem.objects.filter(cart__user=user).count() == 0

    expire_unpaid_online_orders(now=NOW)

    line = CartItem.objects.get(cart__user=user)
    assert line.variant_id == row.pk
    assert line.quantity == order.items.get().quantity_ordered


def test_expiry_sends_the_payment_failed_event_after_commit(
    django_capture_on_commit_callbacks,
) -> None:
    _user, order, _row = _aged_online_order()
    calls: list[tuple[int, str]] = []

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "apps.payments.services.notify_order_event",
            lambda order_id, event: calls.append((order_id, event)),
        )
        with django_capture_on_commit_callbacks(execute=True):
            expire_unpaid_online_orders(now=NOW)

    assert calls == [(order.pk, EVENT_PAYMENT_FAILED)]


def test_expiry_sends_only_the_payment_failed_message(
    django_capture_on_commit_callbacks,
) -> None:
    # The order was never confirmed, so the customer must not also receive the
    # ordinary "order cancelled" message (D77).
    _user, order, _row = _aged_online_order()
    calls: list[tuple[int, str]] = []

    def record(order_id: int, event: str) -> None:
        calls.append((order_id, event))

    with pytest.MonkeyPatch.context() as monkeypatch:
        # Both modules imported the helper by name, so both names are replaced.
        monkeypatch.setattr("apps.orders.services.notify_order_event", record)
        monkeypatch.setattr("apps.payments.services.notify_order_event", record)
        with django_capture_on_commit_callbacks(execute=True):
            expire_unpaid_online_orders(now=NOW)

    assert calls == [(order.pk, EVENT_PAYMENT_FAILED)]


def test_a_fresh_order_is_left_alone() -> None:
    _user, order, _row = _aged_online_order()
    _age(order, minutes=10)

    cancelled = expire_unpaid_online_orders(now=NOW)

    order.refresh_from_db()
    assert cancelled == 0
    assert order.status == Order.Status.PAYMENT_PENDING


def test_the_cutoff_includes_the_reservation_window_and_grace() -> None:
    # The default reservation window is 15 minutes and the grace is 30, so an
    # order exactly 45 minutes old is old enough and one second younger is not.
    _user, order, _row = _aged_online_order()
    _age(order, minutes=45)
    younger = OrderFactory(
        user=order.user,
        status=Order.Status.PAYMENT_PENDING,
        payment_method=Order.PaymentMethod.ONLINE,
        online_amount=order.online_amount,
        amount_due_on_delivery=order.amount_due_on_delivery,
    )
    _age(younger, minutes=44, seconds=59)

    cancelled = expire_unpaid_online_orders(now=NOW)

    order.refresh_from_db()
    younger.refresh_from_db()
    assert cancelled == 1
    assert order.status == Order.Status.CANCELLED
    assert younger.status == Order.Status.PAYMENT_PENDING


def test_a_cod_order_is_never_touched() -> None:
    order = OrderFactory(payment_method=Order.PaymentMethod.COD, status=Order.Status.PLACED)
    _age(order)

    cancelled = expire_unpaid_online_orders(now=NOW)

    order.refresh_from_db()
    assert cancelled == 0
    assert order.status == Order.Status.PLACED


def test_an_order_with_a_captured_payment_is_not_cancelled() -> None:
    _user, order, _row = _aged_online_order()
    CapturedPaymentFactory(order=order)

    cancelled = expire_unpaid_online_orders(now=NOW)

    order.refresh_from_db()
    assert cancelled == 0
    assert order.status == Order.Status.PAYMENT_PENDING


def test_a_confirmed_order_is_not_cancelled() -> None:
    order = OrderFactory(
        payment_method=Order.PaymentMethod.ONLINE,
        status=Order.Status.CONFIRMED,
        online_amount=Decimal("230.00"),
        amount_due_on_delivery=Decimal("0.00"),
    )
    _age(order)

    cancelled = expire_unpaid_online_orders(now=NOW)

    order.refresh_from_db()
    assert cancelled == 0
    assert order.status == Order.Status.CONFIRMED


def test_expiry_is_idempotent() -> None:
    _user, order, _row = _aged_online_order()

    first = expire_unpaid_online_orders(now=NOW)
    second = expire_unpaid_online_orders(now=NOW)

    assert first == 1
    assert second == 0
    assert order.status_logs.filter(to_status=Order.Status.CANCELLED).count() == 1


def test_expiry_logs_how_many_orders_it_cancelled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _user, _order, _row = _aged_online_order()

    with caplog.at_level(logging.INFO, logger="apps.payments.services"):
        expire_unpaid_online_orders(now=NOW)

    assert "Cancelled 1 unpaid online order(s)." in caplog.text


def test_expiry_moves_the_order_updated_at() -> None:
    _user, order, _row = _aged_online_order()
    before = order.updated_at

    expire_unpaid_online_orders(now=NOW)

    order.refresh_from_db()
    assert order.updated_at > before
