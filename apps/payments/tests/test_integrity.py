"""Tests for the online money integrity check (D78)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.management import CommandError, call_command

from apps.orders.models import Order
from apps.orders.tests.factories import OrderFactory
from apps.payments.integrity import check_payment_integrity
from apps.payments.models import Payment, Refund
from apps.payments.tests.factories import CapturedPaymentFactory, RefundFactory
from apps.payments.tests.helpers import NOW

pytestmark = pytest.mark.django_db

CUSTOMER_ONLINE = {
    "payment_method": Order.PaymentMethod.ONLINE,
    "online_amount": Decimal("230.00"),
    "amount_due_on_delivery": Decimal("0.00"),
}


def _payment() -> Payment:
    order = OrderFactory(status=Order.Status.CANCELLED, cancelled_at=NOW, **CUSTOMER_ONLINE)
    return CapturedPaymentFactory(order=order)


def test_a_clean_ledger_has_no_problems() -> None:
    payment = _payment()
    RefundFactory(
        payment=payment,
        status=Refund.Status.PROCESSED,
        amount=payment.amount,
        processed_at=payment.captured_at,
    )

    assert check_payment_integrity() == []


def test_a_refund_that_does_not_add_up_is_reported() -> None:
    payment = _payment()
    RefundFactory(payment=payment, amount=Decimal("200.00"), fee_amount=Decimal("0.00"))

    codes = [problem.code for problem in check_payment_integrity()]

    assert "refund_total_mismatch" in codes


def test_captured_money_on_a_cancelled_order_needs_a_refund() -> None:
    _payment()

    codes = [problem.code for problem in check_payment_integrity()]

    assert codes == ["cancelled_order_money_not_refunded"]


def test_a_refunded_payment_must_not_still_need_a_refund() -> None:
    payment = _payment()
    payment.needs_refund = True
    payment.save(update_fields=["needs_refund", "updated_at"])
    RefundFactory(
        payment=payment,
        status=Refund.Status.PROCESSED,
        amount=payment.amount,
        processed_at=payment.captured_at,
    )

    codes = [problem.code for problem in check_payment_integrity()]

    assert "refunded_payment_still_flagged" in codes


def test_an_open_refund_keeps_the_payment_flag() -> None:
    payment = _payment()
    RefundFactory(
        payment=payment,
        status=Refund.Status.REQUESTED,
        amount=payment.amount,
    )

    codes = [problem.code for problem in check_payment_integrity()]

    assert "open_refund_flag_cleared" in codes


def test_the_command_reports_a_clean_ledger(capsys) -> None:
    call_command("check_payment_integrity")

    assert "no problems found" in capsys.readouterr().out


def test_the_command_exits_with_an_error_on_a_problem() -> None:
    _payment()

    with pytest.raises(CommandError):
        call_command("check_payment_integrity")
