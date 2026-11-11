"""View and service tests for the staff refund actions (D79 amendment)."""

from __future__ import annotations

import threading
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.orders.models import Order
from apps.orders.tests.factories import OrderFactory
from apps.orders.tests.staff_helpers import staff
from apps.payments.fake_gateway import FakeGateway
from apps.payments.models import Payment, Refund
from apps.payments.refunds import send_refund
from apps.payments.tests.factories import CapturedPaymentFactory, RefundFactory

pytestmark = pytest.mark.django_db


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _cancelled_online_order() -> tuple[Order, Payment]:
    """A cancelled order with one captured online payment."""
    order = OrderFactory(
        status=Order.Status.CANCELLED,
        cancelled_at=timezone.now(),
        payment_method=Order.PaymentMethod.ONLINE,
        online_amount=Decimal("230.00"),
        amount_due_on_delivery=Decimal("0.00"),
    )
    payment = CapturedPaymentFactory(order=order, needs_refund=True)
    return order, payment


def _url(name: str, order: Order) -> str:
    return reverse(name, args=[order.order_number])


def test_send_refund_now_calls_send_refund() -> None:
    order, payment = _cancelled_online_order()
    refund = RefundFactory(
        payment=payment,
        status=Refund.Status.REQUESTED,
        amount=Decimal("230.00"),
    )

    with patch("apps.payments.staff_views.send_refund", return_value=refund) as send:
        response = _client(staff(User.ROLE_MANAGER)).post(_url("staff_send_refund", order))

    assert response.status_code == 302
    send.assert_called_once_with(refund.pk)


def test_send_refund_now_needs_refund_payments() -> None:
    order, payment = _cancelled_online_order()
    RefundFactory(payment=payment, status=Refund.Status.REQUESTED)

    with patch("apps.payments.staff_views.send_refund") as send:
        response = _client(staff(User.ROLE_PACKER)).post(_url("staff_send_refund", order))

    assert response.status_code == 403
    send.assert_not_called()


def test_send_refund_now_on_failed_refund_retries(settings) -> None:
    settings.PAYMENT_PROVIDER = "fake"
    FakeGateway.reset()
    order, payment = _cancelled_online_order()
    refund = RefundFactory(
        payment=payment,
        status=Refund.Status.FAILED,
        amount=Decimal("230.00"),
        failure_reason="The provider refused it",
    )

    response = _client(staff(User.ROLE_MANAGER)).post(_url("staff_send_refund", order))

    assert response.status_code == 302
    refund.refresh_from_db()
    assert refund.status == Refund.Status.PROCESSED
    assert refund.attempt_count == 1


def test_mark_refunded_by_hand_requires_a_note() -> None:
    order, payment = _cancelled_online_order()
    refund = RefundFactory(payment=payment, status=Refund.Status.REQUESTED)

    response = _client(staff(User.ROLE_MANAGER)).post(
        _url("staff_mark_refunded_by_hand", order),
        {"note": "   "},
        follow=True,
    )

    assert response.status_code == 200
    assert "needs a note" in response.content.decode()
    refund.refresh_from_db()
    assert refund.status == Refund.Status.REQUESTED


def test_mark_refunded_by_hand_records_actor_and_time() -> None:
    actor = staff(User.ROLE_MANAGER)
    order, payment = _cancelled_online_order()
    refund = RefundFactory(
        payment=payment,
        status=Refund.Status.FAILED,
        failure_reason="The provider refused it",
    )
    refund_before = refund.updated_at
    payment_before = payment.updated_at

    response = _client(actor).post(
        _url("staff_mark_refunded_by_hand", order),
        {"note": "Paid by UPI at the counter"},
    )

    assert response.status_code == 302
    refund.refresh_from_db()
    payment.refresh_from_db()
    assert refund.status == Refund.Status.PROCESSED
    assert refund.marked_by_id == actor.pk
    assert refund.marked_at is not None
    assert refund.marked_note == "Paid by UPI at the counter"
    assert refund.updated_at > refund_before
    assert payment.needs_refund is False
    assert payment.updated_at > payment_before


def test_mark_refunded_by_hand_makes_no_gateway_call() -> None:
    order, payment = _cancelled_online_order()
    RefundFactory(payment=payment, status=Refund.Status.REQUESTED)

    with patch("apps.payments.refunds.get_gateway") as gateway:
        response = _client(staff(User.ROLE_MANAGER)).post(
            _url("staff_mark_refunded_by_hand", order),
            {"note": "Cash returned at the counter"},
        )

    assert response.status_code == 302
    gateway.assert_not_called()


def test_mark_refunded_by_hand_twice_is_refused() -> None:
    order, payment = _cancelled_online_order()
    refund = RefundFactory(payment=payment, status=Refund.Status.REQUESTED)
    client = _client(staff(User.ROLE_MANAGER))
    url = _url("staff_mark_refunded_by_hand", order)

    client.post(url, {"note": "First record"})
    response = client.post(url, {"note": "Second record"}, follow=True)

    assert response.status_code == 200
    assert "already processed" in response.content.decode()
    refund.refresh_from_db()
    assert refund.status == Refund.Status.PROCESSED
    assert refund.marked_note == "First record"


class BlockingGateway(FakeGateway):
    """A fake provider that stops inside its refund call until released."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def create_refund(self, gateway_payment_id, amount, idempotency_key):
        self.calls += 1
        self.entered.set()
        self.release.wait(timeout=10)
        return super().create_refund(gateway_payment_id, amount, idempotency_key)


@pytest.mark.django_db(transaction=True)
def test_two_workers_cannot_send_the_same_refund() -> None:
    FakeGateway.reset()
    _order, payment = _cancelled_online_order()
    refund = RefundFactory(
        payment=payment,
        status=Refund.Status.REQUESTED,
        amount=Decimal("230.00"),
    )
    gateway = BlockingGateway()
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            send_refund(refund.pk, gateway=gateway)
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=worker)
    thread.start()
    assert gateway.entered.wait(timeout=10)

    # The first worker is inside the provider call, so the claim has committed
    # SENT. The second worker must stop at the claim.
    second = send_refund(refund.pk, gateway=gateway)
    assert second.status == Refund.Status.SENT

    gateway.release.set()
    thread.join(timeout=30)

    assert not errors, errors
    refund.refresh_from_db()
    assert gateway.calls == 1
    assert refund.attempt_count == 1
    assert refund.status == Refund.Status.PROCESSED
