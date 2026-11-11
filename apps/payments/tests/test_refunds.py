"""Tests for the refund service (D78)."""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection, transaction
from django.utils import timezone

from apps.orders.models import Order
from apps.orders.tests.factories import OrderFactory
from apps.payments.fake_gateway import FakeGateway
from apps.payments.gateway import (
    REFUND_PROCESSED,
    GatewayRefund,
    RefundRejectedError,
)
from apps.payments.models import Payment, Refund
from apps.payments.refunds import (
    RefundNotAllowedError,
    ensure_refund,
    get_refund,
    latest_refund,
    reconcile_refunds,
    refresh_refund_from_gateway,
    send_refund,
)
from apps.payments.tests.factories import (
    CapturedPaymentFactory,
    PaymentFactory,
    RefundFactory,
)
from apps.payments.tests.helpers import NOW, clear_settings, reset_settings

pytestmark = pytest.mark.django_db

CUSTOMER_ONLINE = {
    "payment_method": Order.PaymentMethod.ONLINE,
    "online_amount": Decimal("230.00"),
    "amount_due_on_delivery": Decimal("0.00"),
}


@pytest.fixture(autouse=True)
def _fake_gateway(settings):
    """A clean shop row and the fake provider around every test."""
    reset_settings()
    settings.PAYMENT_PROVIDER = "fake"
    FakeGateway.reset()
    yield
    FakeGateway.reset()
    clear_settings()


def _payment(*, fee_percent: str = "0.00", amount: str = "230.00") -> Payment:
    order = OrderFactory(
        status=Order.Status.CANCELLED,
        cancelled_at=NOW,
        refund_fee_percent=Decimal(fee_percent),
        **CUSTOMER_ONLINE,
    )
    return CapturedPaymentFactory(order=order, amount=Decimal(amount))


def test_a_shop_cancellation_returns_the_whole_payment() -> None:
    payment = _payment()
    before = payment.updated_at

    refund = ensure_refund(payment, kind=Refund.Kind.SHOP_CANCEL, now=NOW)

    payment.refresh_from_db()
    assert refund.amount == payment.amount
    assert refund.fee_amount == Decimal("0.00")
    assert refund.status == Refund.Status.REQUESTED
    assert refund.idempotency_key == f"refund-{payment.pk}"
    assert refund.amount + refund.fee_amount == payment.amount
    # The payment row records the refund duty, so its timestamp must move.
    assert payment.needs_refund is True
    assert payment.updated_at > before


def test_a_customer_cancellation_keeps_the_order_fee_percent() -> None:
    payment = _payment(fee_percent="2.50")

    refund = ensure_refund(payment, kind=Refund.Kind.CUSTOMER_CANCEL, now=NOW)

    assert refund.fee_amount == Decimal("5.75")
    assert refund.amount == Decimal("224.25")
    assert refund.amount + refund.fee_amount == payment.amount


def test_the_fee_is_rounded_half_up_to_the_paisa() -> None:
    payment = _payment(fee_percent="0.50", amount="1.00")

    refund = ensure_refund(payment, kind=Refund.Kind.CUSTOMER_CANCEL, now=NOW)

    # 0.50% of 1.00 is 0.005, which rounds up to one paisa.
    assert refund.fee_amount == Decimal("0.01")
    assert refund.amount == Decimal("0.99")


def test_a_second_call_returns_the_same_refund() -> None:
    payment = _payment()

    first = ensure_refund(payment, kind=Refund.Kind.SHOP_CANCEL, now=NOW)
    second = ensure_refund(payment, kind=Refund.Kind.CUSTOMER_CANCEL, now=NOW)

    assert second.pk == first.pk
    assert Refund.objects.filter(payment=payment).count() == 1


def test_an_uncaptured_payment_cannot_be_refunded() -> None:
    payment = PaymentFactory(order=OrderFactory(**CUSTOMER_ONLINE))

    with pytest.raises(RefundNotAllowedError):
        ensure_refund(payment, kind=Refund.Kind.SHOP_CANCEL, now=NOW)


def test_an_unknown_kind_is_refused() -> None:
    payment = _payment()

    with pytest.raises(RefundNotAllowedError):
        ensure_refund(payment, kind="SOMETHING_ELSE", now=NOW)


def test_a_sent_refund_is_processed_when_the_provider_says_so() -> None:
    payment = _payment()
    refund = ensure_refund(payment, kind=Refund.Kind.SHOP_CANCEL, now=NOW)
    payment.refresh_from_db()
    before = payment.updated_at

    stored = send_refund(refund.pk, gateway=FakeGateway(), now=NOW)

    payment.refresh_from_db()
    assert stored.status == Refund.Status.PROCESSED
    assert stored.attempt_count == 1
    assert stored.sent_at is not None
    assert stored.processed_at is not None
    # The provider answer is a real write, so the refund timestamp moves.
    assert stored.updated_at > refund.updated_at
    assert stored.provider_refund_id.startswith("fake_refund_")
    # The refund duty ends only when the provider says the money went back.
    assert payment.needs_refund is False
    # Clearing the duty is a real write on the payment row.
    assert payment.updated_at > before


def test_a_second_send_after_success_changes_nothing() -> None:
    payment = _payment()
    refund = ensure_refund(payment, kind=Refund.Kind.SHOP_CANCEL, now=NOW)

    send_refund(refund.pk, gateway=FakeGateway(), now=NOW)
    again = send_refund(refund.pk, gateway=FakeGateway(), now=NOW)

    assert again.status == Refund.Status.PROCESSED
    assert again.attempt_count == 1


def test_a_transient_provider_error_keeps_the_refund_requested() -> None:
    payment = _payment()
    payment.needs_refund = True
    payment.save(update_fields=["needs_refund", "updated_at"])
    refund = ensure_refund(payment, kind=Refund.Kind.SHOP_CANCEL, now=NOW)
    FakeGateway.fail_refund(refund.idempotency_key)

    stored = send_refund(refund.pk, gateway=FakeGateway(), now=NOW)

    assert stored.status == Refund.Status.REQUESTED
    assert stored.attempt_count == 1
    assert stored.failure_reason


def test_the_last_allowed_attempt_marks_the_refund_failed(settings) -> None:
    settings.REFUND_MAX_ATTEMPTS = 1
    payment = _payment()
    refund = ensure_refund(payment, kind=Refund.Kind.SHOP_CANCEL, now=NOW)
    FakeGateway.fail_refund(refund.idempotency_key)

    stored = send_refund(refund.pk, gateway=FakeGateway(), now=NOW)

    assert stored.status == Refund.Status.FAILED
    assert stored.failure_reason


def test_a_provider_refusal_fails_the_refund_at_once() -> None:
    class RejectingGateway(FakeGateway):
        def create_refund(self, gateway_payment_id, amount, idempotency_key):
            raise RefundRejectedError("The provider refused the refund.")

    payment = _payment()
    refund = ensure_refund(payment, kind=Refund.Kind.SHOP_CANCEL, now=NOW)

    stored = send_refund(refund.pk, gateway=RejectingGateway(), now=NOW)

    assert stored.status == Refund.Status.FAILED
    assert stored.attempt_count == 1


def test_a_failed_refund_is_not_sent_again() -> None:
    payment = _payment()
    refund = RefundFactory(
        payment=payment,
        status=Refund.Status.FAILED,
        failure_reason="Done",
    )

    stored = send_refund(refund.pk, gateway=FakeGateway(), now=NOW)

    assert stored.status == Refund.Status.FAILED
    assert stored.attempt_count == 0


def test_a_wrong_provider_amount_changes_no_money() -> None:
    class WrongAmountGateway(FakeGateway):
        def create_refund(self, gateway_payment_id, amount, idempotency_key):
            return GatewayRefund(
                gateway_refund_id="fake_refund_wrong",
                gateway_payment_id=gateway_payment_id,
                kind=REFUND_PROCESSED,
                amount=amount - Decimal("1.00"),
            )

    payment = _payment()
    refund = ensure_refund(payment, kind=Refund.Kind.SHOP_CANCEL, now=NOW)

    stored = send_refund(refund.pk, gateway=WrongAmountGateway(), now=NOW)

    payment.refresh_from_db()
    assert stored.status == Refund.Status.REQUESTED
    assert stored.processed_at is None
    assert stored.failure_reason == "The provider refunded a different amount."
    assert payment.needs_refund is True


def test_a_processed_refund_never_moves_again() -> None:
    payment = _payment()
    refund = RefundFactory(
        payment=payment,
        status=Refund.Status.PROCESSED,
        processed_at=NOW,
    )

    stored = send_refund(refund.pk, gateway=FakeGateway(), now=NOW)

    assert stored.status == Refund.Status.PROCESSED
    assert stored.attempt_count == 0


def test_refresh_stores_the_provider_answer_for_a_sent_refund() -> None:
    payment = _payment()
    refund = RefundFactory(
        payment=payment,
        status=Refund.Status.SENT,
        amount=payment.amount,
        sent_at=NOW,
        last_attempt_at=NOW,
        provider_refund_id="fake_refund_known",
    )
    FakeGateway.seed_refunds(
        payment.gateway_payment_id,
        [
            GatewayRefund(
                gateway_refund_id="fake_refund_known",
                gateway_payment_id=payment.gateway_payment_id,
                kind=REFUND_PROCESSED,
                amount=payment.amount,
            )
        ],
    )

    changed = refresh_refund_from_gateway(refund.pk, gateway=FakeGateway(), now=NOW)

    payment.refresh_from_db()
    refund.refresh_from_db()
    assert changed is True
    assert refund.status == Refund.Status.PROCESSED
    assert refund.processed_at == NOW
    assert payment.needs_refund is False


def test_refresh_without_a_provider_answer_writes_nothing() -> None:
    payment = _payment()
    refund = RefundFactory(
        payment=payment,
        status=Refund.Status.SENT,
        amount=payment.amount,
    )

    changed = refresh_refund_from_gateway(refund.pk, gateway=FakeGateway(), now=NOW)

    refund.refresh_from_db()
    assert changed is False
    assert refund.status == Refund.Status.SENT


def test_reconcile_sends_a_requested_refund() -> None:
    payment = _payment()
    payment.needs_refund = True
    payment.save(update_fields=["needs_refund", "updated_at"])
    refund = ensure_refund(payment, kind=Refund.Kind.SHOP_CANCEL, now=NOW)

    handled = reconcile_refunds(now=NOW)

    payment.refresh_from_db()
    refund.refresh_from_db()
    assert handled >= 1
    assert refund.status == Refund.Status.PROCESSED
    assert payment.needs_refund is False


def test_reconcile_creates_the_missing_refund_for_captured_money() -> None:
    user = OrderFactory().user
    order = OrderFactory(
        user=user,
        status=Order.Status.CANCELLED,
        cancelled_at=NOW,
        **CUSTOMER_ONLINE,
    )
    old = NOW - timedelta(hours=1)
    payment = CapturedPaymentFactory(
        order=order,
        needs_refund=True,
        captured_at=old,
    )

    reconcile_refunds(now=NOW)

    refund = Refund.objects.get(payment=payment)
    assert refund.kind == Refund.Kind.SHOP_CANCEL
    assert refund.amount == payment.amount


def test_reconcile_checks_a_stale_sent_refund() -> None:
    payment = _payment()
    old = NOW - timedelta(hours=2)
    refund = RefundFactory(
        payment=payment,
        status=Refund.Status.SENT,
        amount=payment.amount,
        sent_at=old,
        last_attempt_at=old,
    )
    FakeGateway.seed_refunds(
        payment.gateway_payment_id,
        [
            GatewayRefund(
                gateway_refund_id="fake_refund_late",
                gateway_payment_id=payment.gateway_payment_id,
                kind=REFUND_PROCESSED,
                amount=payment.amount,
            )
        ],
    )

    reconcile_refunds(now=NOW)

    refund.refresh_from_db()
    assert refund.status == Refund.Status.PROCESSED


def test_get_refund_and_latest_refund_read_the_stored_rows() -> None:
    payment = _payment()
    refund = RefundFactory(payment=payment, amount=payment.amount)

    assert get_refund(refund.pk).pk == refund.pk
    assert latest_refund(payment.order).pk == refund.pk
    assert latest_refund(OrderFactory()) is None


def test_the_refund_timestamps_are_real_clock_values() -> None:
    payment = _payment()
    refund = ensure_refund(payment, kind=Refund.Kind.SHOP_CANCEL, now=NOW)

    assert refund.created_at <= timezone.now()
    assert refund.attempt_count == 0


@pytest.mark.django_db(transaction=True)
def test_send_waits_for_the_refund_row_lock() -> None:
    payment = _payment()
    refund = ensure_refund(payment, kind=Refund.Kind.SHOP_CANCEL, now=NOW)
    refund_pk = refund.pk
    locked = threading.Event()
    errors: list[BaseException] = []

    def hold_the_refund() -> None:
        """Mark the refund FAILED inside a transaction that holds its row lock."""
        try:
            with transaction.atomic():
                held = Refund.objects.select_for_update().get(pk=refund_pk)
                locked.set()
                time.sleep(0.5)
                held.status = Refund.Status.FAILED
                held.failure_reason = "Closed by the other transaction"
                held.save(update_fields=["status", "failure_reason", "updated_at"])
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    holder = threading.Thread(target=hold_the_refund)
    holder.start()
    assert locked.wait(timeout=10)
    stored = send_refund(refund_pk, gateway=FakeGateway(), now=NOW)
    holder.join(timeout=30)

    assert not errors, errors
    # The claim read the committed FAILED row only after the lock was released,
    # so no provider call was made and no attempt was counted.
    assert stored.status == Refund.Status.FAILED
    assert stored.attempt_count == 0
    assert FakeGateway().fetch_payment_refunds(payment.gateway_payment_id) == []
