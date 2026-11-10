"""Tests for cancelling an order that has an online payment (D77)."""

from __future__ import annotations

import threading
import time
from decimal import Decimal

import pytest
from django.db import connection, transaction

from apps.orders.models import Order
from apps.orders.services import (
    InvalidTransitionError,
    OrderNotFoundError,
    OrderPaidOnlineError,
    cancel_order_by_customer,
    transition_order,
)
from apps.orders.tests.factories import OrderFactory
from apps.payments.models import Payment
from apps.payments.tests.factories import CapturedPaymentFactory
from apps.payments.tests.helpers import NOW, clear_settings, reset_settings
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before every test."""
    reset_settings()
    yield
    clear_settings()


def _paid_order(user, **overrides):
    """A confirmed online order whose online part is already captured."""
    fields = {
        "user": user,
        "status": Order.Status.CONFIRMED,
        "payment_method": Order.PaymentMethod.ONLINE,
        "online_amount": Decimal("230.00"),
        "amount_due_on_delivery": Decimal("0.00"),
    }
    fields.update(overrides)
    order = OrderFactory(**fields)
    payment = CapturedPaymentFactory(order=order)
    return order, payment


def test_a_customer_cannot_cancel_an_order_that_is_paid_online() -> None:
    user = UserFactory()
    order, payment = _paid_order(user)

    with pytest.raises(OrderPaidOnlineError):
        cancel_order_by_customer(user, order.pk, now=NOW)

    order.refresh_from_db()
    payment.refresh_from_db()
    assert order.status == Order.Status.CONFIRMED
    assert payment.status == Payment.Status.CAPTURED
    assert payment.needs_refund is False


def test_staff_can_cancel_a_paid_online_order_and_it_asks_for_a_refund() -> None:
    user = UserFactory()
    staff = UserFactory(is_staff=True)
    order, payment = _paid_order(user)

    cancelled = transition_order(
        order.pk,
        Order.Status.CANCELLED,
        actor=staff,
        reason="Customer called the shop",
        now=NOW,
    )

    payment.refresh_from_db()
    assert cancelled.status == Order.Status.CANCELLED
    assert payment.status == Payment.Status.CAPTURED
    assert payment.needs_refund is True


def test_a_staff_cancellation_moves_the_payment_updated_at() -> None:
    user = UserFactory()
    order, payment = _paid_order(user)
    before = payment.updated_at

    transition_order(
        order.pk,
        Order.Status.CANCELLED,
        actor=UserFactory(is_staff=True),
        reason="Test",
        now=NOW,
    )

    payment.refresh_from_db()
    assert payment.updated_at > before


def test_a_customer_cannot_cancel_a_payment_pending_order() -> None:
    user = UserFactory()
    order = OrderFactory(
        user=user,
        status=Order.Status.PAYMENT_PENDING,
        payment_method=Order.PaymentMethod.ONLINE,
        online_amount=Decimal("230.00"),
        amount_due_on_delivery=Decimal("0.00"),
    )

    with pytest.raises(InvalidTransitionError):
        cancel_order_by_customer(user, order.pk, now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.PAYMENT_PENDING


def test_a_customer_cannot_cancel_a_payment_failed_order() -> None:
    user = UserFactory()
    order = OrderFactory(
        user=user,
        status=Order.Status.PAYMENT_FAILED,
        payment_method=Order.PaymentMethod.ONLINE,
        online_amount=Decimal("230.00"),
        amount_due_on_delivery=Decimal("0.00"),
    )

    with pytest.raises(InvalidTransitionError):
        cancel_order_by_customer(user, order.pk, now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.PAYMENT_FAILED


def test_a_customer_can_still_cancel_a_confirmed_order_with_no_capture() -> None:
    user = UserFactory()
    order = OrderFactory(
        user=user,
        status=Order.Status.CONFIRMED,
        payment_method=Order.PaymentMethod.ONLINE,
        online_amount=Decimal("230.00"),
        amount_due_on_delivery=Decimal("0.00"),
    )

    cancelled = cancel_order_by_customer(user, order.pk, reason="Changed my mind", now=NOW)

    assert cancelled.status == Order.Status.CANCELLED
    assert cancelled.cancel_reason == "Changed my mind"


def test_another_customers_order_is_not_found() -> None:
    order, _payment = _paid_order(UserFactory())

    with pytest.raises(OrderNotFoundError):
        cancel_order_by_customer(UserFactory(), order.pk, now=NOW)


def test_an_already_cancelled_order_is_refused() -> None:
    user = UserFactory()
    order = OrderFactory(
        user=user,
        status=Order.Status.CANCELLED,
        cancelled_at=NOW,
        cancel_reason="Already gone",
        payment_method=Order.PaymentMethod.ONLINE,
        online_amount=Decimal("230.00"),
        amount_due_on_delivery=Decimal("0.00"),
    )

    with pytest.raises(InvalidTransitionError):
        cancel_order_by_customer(user, order.pk, now=NOW)


@pytest.mark.django_db(transaction=True)
def test_a_cancellation_waits_for_the_payment_row_lock() -> None:
    # The refund flag is a read-modify-write on the payment row (D77), so two
    # transactions must queue on that row instead of losing one write.
    order, payment = _paid_order(UserFactory())
    payment_pk = payment.pk
    order_pk = order.pk
    locked = threading.Event()
    errors: list[BaseException] = []

    def hold_the_payment() -> None:
        try:
            with transaction.atomic():
                held = Payment.objects.select_for_update().get(pk=payment_pk)
                locked.set()
                time.sleep(0.5)
                # A stale write from another transaction: without the lock on
                # the cancel side this commit would erase the refund flag.
                held.needs_refund = False
                held.save(update_fields=["needs_refund", "updated_at"])
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    holder = threading.Thread(target=hold_the_payment)
    holder.start()
    assert locked.wait(timeout=10)
    transition_order(
        order_pk,
        Order.Status.CANCELLED,
        actor=None,
        reason="Staff cancel",
        now=NOW,
    )
    holder.join(timeout=30)

    assert not errors, errors
    payment.refresh_from_db()
    assert payment.needs_refund is True
