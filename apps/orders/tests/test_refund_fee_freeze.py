"""Tests for the fee percent frozen on an order and the refund hand-off (D78)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.orders.models import Order
from apps.orders.services import cancel_order_by_customer, transition_order
from apps.orders.tests.factories import OrderFactory
from apps.payments.models import Payment, Refund
from apps.payments.tests.factories import CapturedPaymentFactory
from apps.payments.tests.helpers import (
    NOW,
    clear_settings,
    place_online,
    ready_customer,
    reset_settings,
    set_settings,
)
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

CUSTOMER_ONLINE = {
    "payment_method": Order.PaymentMethod.ONLINE,
    "online_amount": Decimal("230.00"),
    "amount_due_on_delivery": Decimal("0.00"),
}


@pytest.fixture(autouse=True)
def _shop_settings_row(settings):
    """Create the settings row, a fake provider and an empty cache per test."""
    reset_settings()
    # The test settings leave PAYMENT_PROVIDER empty, so place_order would
    # refuse an online part; the fake provider is the one the tests expect.
    settings.PAYMENT_PROVIDER = "fake"
    yield
    clear_settings()


def _paid_order() -> tuple[Order, Payment]:
    """A confirmed online order whose online part is already captured."""
    order = OrderFactory(status=Order.Status.CONFIRMED, **CUSTOMER_ONLINE)
    payment = CapturedPaymentFactory(order=order)
    return order, payment


def test_place_order_copies_the_shop_fee_percent_onto_the_order() -> None:
    set_settings(refund_fee_percent=Decimal("2.00"))
    user, address_row, _row = ready_customer()

    placed = place_online(user, address_row, now=NOW)

    placed.order.refresh_from_db()
    assert placed.order.refund_fee_percent == Decimal("2.00")


def test_changing_the_shop_fee_percent_later_does_not_change_the_order() -> None:
    set_settings(refund_fee_percent=Decimal("2.00"))
    user, address_row, _row = ready_customer()
    placed = place_online(user, address_row, now=NOW)

    set_settings(refund_fee_percent=Decimal("5.00"))
    placed.order.refresh_from_db()

    assert placed.order.refund_fee_percent == Decimal("2.00")


def test_a_customer_cancel_uses_the_percent_frozen_on_the_order() -> None:
    user = UserFactory()
    order = OrderFactory(
        user=user,
        status=Order.Status.CONFIRMED,
        refund_fee_percent=Decimal("2.00"),
        **CUSTOMER_ONLINE,
    )
    payment = CapturedPaymentFactory(order=order)
    # The shop has moved on to 5 percent, but this order keeps its own 2.
    set_settings(refund_fee_percent=Decimal("5.00"))

    cancel_order_by_customer(user, order.pk, now=NOW)

    refund = Refund.objects.get(payment=payment)
    # 2 percent of 230.00 is 4.60, so 225.40 goes back and the two add up.
    assert refund.fee_amount == Decimal("4.60")
    assert refund.amount == Decimal("225.40")
    assert refund.amount + refund.fee_amount == payment.amount


def test_the_refund_task_is_not_queued_before_the_cancel_commits(
    django_capture_on_commit_callbacks,
    monkeypatch,
) -> None:
    calls: list[int] = []
    monkeypatch.setattr("apps.payments.tasks.send_refunded_payment.delay", calls.append)
    order, _payment = _paid_order()

    # notify=False keeps the cancelled-order notice out, so the refund task is
    # the only callback waiting; the refund itself is queued the same way.
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        transition_order(
            order.pk,
            Order.Status.CANCELLED,
            actor=UserFactory(is_staff=True),
            reason="Stock is damaged",
            now=NOW,
            notify=False,
        )

    assert calls == []
    assert len(callbacks) == 1


def test_the_refund_task_is_queued_once_after_the_cancel_commits(
    django_capture_on_commit_callbacks,
    monkeypatch,
) -> None:
    calls: list[int] = []
    monkeypatch.setattr("apps.payments.tasks.send_refunded_payment.delay", calls.append)
    order, payment = _paid_order()

    with django_capture_on_commit_callbacks(execute=True):
        transition_order(
            order.pk,
            Order.Status.CANCELLED,
            actor=UserFactory(is_staff=True),
            reason="Stock is damaged",
            now=NOW,
            notify=False,
        )

    refund = Refund.objects.get(payment=payment)
    assert calls == [refund.pk]
