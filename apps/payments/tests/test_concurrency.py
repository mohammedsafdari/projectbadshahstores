"""Locking tests for online payments (D77).

Each test starts real threads on their own database connections. The assertions
are on the final rows, never on timing or on which thread won.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from datetime import timedelta

import pytest
from django.db import connection, transaction

from apps.accounts.models import User
from apps.orders.models import Order, OrderStatusLog
from apps.payments.models import Payment, WebhookEvent
from apps.payments.services import (
    PaymentNotAllowedError,
    expire_unpaid_online_orders,
    handle_gateway_event,
    start_online_payment,
)
from apps.payments.tests.helpers import (
    NOW,
    clear_settings,
    event_for,
    online_order,
    reset_settings,
    start_payment,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _online_shop(settings):
    """A clean settings row and the fake provider before any thread runs."""
    reset_settings()
    settings.PAYMENT_PROVIDER = "fake"
    yield
    clear_settings()


def test_start_online_payment_waits_for_the_order_lock() -> None:
    _user, order, _row = online_order()
    order_pk = order.pk
    order_number = order.order_number
    user_pk = order.user_id
    locked = threading.Event()
    errors: list[BaseException] = []
    outcomes: list[str] = []

    def hold_the_order() -> None:
        try:
            with transaction.atomic():
                held = Order.objects.select_for_update().get(pk=order_pk)
                held.status = Order.Status.CANCELLED
                held.cancelled_at = NOW
                held.cancel_reason = "Holder"
                held.save(update_fields=["status", "cancelled_at", "cancel_reason", "updated_at"])
                locked.set()
                time.sleep(0.5)
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    def start_while_locked() -> None:
        try:
            assert locked.wait(timeout=10)
            thread_user = User.objects.get(pk=user_pk)
            try:
                start_online_payment(thread_user, order_number, now=NOW)
            except PaymentNotAllowedError:
                outcomes.append("refused")
            else:
                outcomes.append("started")
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    holder = threading.Thread(target=hold_the_order)
    starter = threading.Thread(target=start_while_locked)
    holder.start()
    starter.start()
    holder.join(timeout=30)
    starter.join(timeout=30)

    assert not errors, errors
    # The start saw the committed cancellation; a start without the lock would
    # have seen PAYMENT_PENDING and created an attempt.
    assert outcomes == ["refused"]
    assert Payment.objects.filter(order_id=order_pk).count() == 0
    assert Order.objects.get(pk=order_pk).status == Order.Status.CANCELLED


def test_handle_gateway_event_waits_for_the_order_lock() -> None:
    _user, order, _row = online_order()
    payment = start_payment(order).payment
    event = event_for(payment, event_id="evt_lock")
    order_pk = order.pk
    payment_pk = payment.pk
    locked = threading.Event()
    errors: list[BaseException] = []

    def hold_the_order() -> None:
        try:
            with transaction.atomic():
                held = Order.objects.select_for_update().get(pk=order_pk)
                held.status = Order.Status.CANCELLED
                held.cancelled_at = NOW
                held.cancel_reason = "Holder"
                held.save(update_fields=["status", "cancelled_at", "cancel_reason", "updated_at"])
                locked.set()
                time.sleep(0.5)
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    def capture_while_locked() -> None:
        try:
            assert locked.wait(timeout=10)
            handle_gateway_event(event, provider="fake", now=NOW)
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    holder = threading.Thread(target=hold_the_order)
    capturer = threading.Thread(target=capture_while_locked)
    holder.start()
    capturer.start()
    holder.join(timeout=30)
    capturer.join(timeout=30)

    assert not errors, errors
    stored = Payment.objects.get(pk=payment_pk)
    final = Order.objects.get(pk=order_pk)
    # Without the lock the capture would have confirmed the order instead.
    assert final.status == Order.Status.CANCELLED
    assert stored.status == Payment.Status.CAPTURED
    assert stored.needs_refund is True


def test_expiry_waits_for_the_order_lock() -> None:
    _user, order, _row = online_order()
    Order.objects.filter(pk=order.pk).update(created_at=NOW - timedelta(minutes=50))
    order_pk = order.pk
    locked = threading.Event()
    errors: list[BaseException] = []
    results: list[int] = []

    def hold_the_order() -> None:
        try:
            with transaction.atomic():
                held = Order.objects.select_for_update().get(pk=order_pk)
                held.status = Order.Status.CONFIRMED
                held.save(update_fields=["status", "updated_at"])
                locked.set()
                time.sleep(0.5)
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    def expire_while_locked() -> None:
        try:
            assert locked.wait(timeout=10)
            results.append(expire_unpaid_online_orders(now=NOW))
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    holder = threading.Thread(target=hold_the_order)
    expirer = threading.Thread(target=expire_while_locked)
    holder.start()
    expirer.start()
    holder.join(timeout=30)
    expirer.join(timeout=30)

    assert not errors, errors
    # The expiry saw the committed CONFIRMED state and left the order alone.
    assert results == [0]
    assert Order.objects.get(pk=order_pk).status == Order.Status.CONFIRMED


def test_two_simultaneous_starts_create_one_payment() -> None:
    _user, order, _row = online_order()
    order_pk = order.pk
    order_number = order.order_number
    user_pk = order.user_id
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker(index: int):
        try:
            barrier.wait(timeout=10)
            thread_user = User.objects.get(pk=user_pk)
            return start_online_payment(thread_user, order_number, now=NOW)
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, index) for index in range(2)]
        results = [future.result() for future in futures]

    assert not errors, errors
    assert Payment.objects.filter(order_id=order_pk).count() == 1
    assert {result.payment.pk for result in results} == {Payment.objects.get(order_id=order_pk).pk}


def test_two_capture_events_confirm_the_order_once() -> None:
    _user, order, _row = online_order()
    payment = start_payment(order).payment
    events = [
        event_for(payment, event_id="evt_first"),
        event_for(payment, event_id="evt_second"),
    ]
    order_pk = order.pk
    payment_pk = payment.pk
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker(index: int):
        try:
            barrier.wait(timeout=10)
            return handle_gateway_event(events[index], provider="fake", now=NOW)
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, index) for index in range(2)]
        for future in futures:
            future.result()

    assert not errors, errors
    stored = Payment.objects.get(pk=payment_pk)
    final = Order.objects.get(pk=order_pk)
    assert final.status == Order.Status.CONFIRMED
    assert stored.status == Payment.Status.CAPTURED
    assert stored.needs_refund is False
    # Both webhooks are stored, but the order was confirmed only once.
    assert WebhookEvent.objects.count() == 2
    assert (
        OrderStatusLog.objects.filter(order_id=order_pk, to_status=Order.Status.PAID).count() == 1
    )
    assert (
        OrderStatusLog.objects.filter(order_id=order_pk, to_status=Order.Status.CONFIRMED).count()
        == 1
    )
