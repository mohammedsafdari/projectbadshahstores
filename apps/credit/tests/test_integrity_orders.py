"""Integrity tests for credit orders, charges and cancel reversals (D68)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.credit.integrity import check_credit_integrity
from apps.credit.models import CreditAccount, CreditEntry
from apps.credit.tests.factories import CreditAccountFactory
from apps.orders.models import Order
from apps.orders.tests.factories import OrderFactory

pytestmark = pytest.mark.django_db

TODAY = date(2026, 11, 7)
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


def _order(
    *,
    payment_method: str,
    status: str = Order.Status.CONFIRMED,
    cancelled_at: datetime | None = None,
) -> Order:
    """One order for a fresh customer, with the factory's 230.00 total."""
    return OrderFactory(
        payment_method=payment_method,
        status=status,
        cancelled_at=cancelled_at,
    )


def _entry(
    account: CreditAccount,
    order: Order,
    *,
    entry_type: str,
    amount: str,
    balance_after: str,
) -> CreditEntry:
    """Write one ledger row straight through the model (the guard blocks edits)."""
    return CreditEntry.objects.create(
        account=account,
        entry_type=entry_type,
        amount=Decimal(amount),
        balance_after=Decimal(balance_after),
        order=order,
        entry_date=TODAY,
    )


def test_integrity_reports_a_credit_order_without_a_charge() -> None:
    order = _order(payment_method=Order.PaymentMethod.CREDIT)
    account = CreditAccountFactory(user=order.user, credit_limit=Decimal("1000.00"))

    problems = check_credit_integrity()

    assert len(problems) == 1
    assert problems[0].account_id == account.pk
    assert problems[0].code == "credit_order_without_charge"


def test_integrity_reports_a_cancelled_credit_order_without_a_reversal() -> None:
    order = _order(
        payment_method=Order.PaymentMethod.CREDIT,
        status=Order.Status.CANCELLED,
        cancelled_at=NOW,
    )
    account = CreditAccountFactory(
        user=order.user,
        credit_limit=Decimal("1000.00"),
        balance=Decimal("230.00"),
    )
    _entry(
        account,
        order,
        entry_type=CreditEntry.EntryType.ORDER,
        amount="230.00",
        balance_after="230.00",
    )

    problems = check_credit_integrity()

    assert len(problems) == 1
    assert problems[0].account_id == account.pk
    assert problems[0].code == "cancelled_credit_order_not_reversed"


def test_integrity_reports_a_charge_on_a_cod_order() -> None:
    order = _order(payment_method=Order.PaymentMethod.COD)
    account = CreditAccountFactory(
        user=order.user,
        credit_limit=Decimal("1000.00"),
        balance=Decimal("230.00"),
    )
    _entry(
        account,
        order,
        entry_type=CreditEntry.EntryType.ORDER,
        amount="230.00",
        balance_after="230.00",
    )

    problems = check_credit_integrity()

    assert len(problems) == 1
    assert problems[0].account_id == account.pk
    assert problems[0].code == "charge_on_non_credit_order"


def test_integrity_is_clean_for_a_charged_credit_order() -> None:
    order = _order(payment_method=Order.PaymentMethod.CREDIT)
    account = CreditAccountFactory(
        user=order.user,
        credit_limit=Decimal("1000.00"),
        balance=Decimal("230.00"),
    )
    _entry(
        account,
        order,
        entry_type=CreditEntry.EntryType.ORDER,
        amount="230.00",
        balance_after="230.00",
    )

    assert check_credit_integrity() == []


def test_integrity_is_clean_for_a_cancelled_and_reversed_credit_order() -> None:
    order = _order(
        payment_method=Order.PaymentMethod.CREDIT,
        status=Order.Status.CANCELLED,
        cancelled_at=NOW,
    )
    account = CreditAccountFactory(
        user=order.user,
        credit_limit=Decimal("1000.00"),
        balance=Decimal("0.00"),
    )
    _entry(
        account,
        order,
        entry_type=CreditEntry.EntryType.ORDER,
        amount="230.00",
        balance_after="230.00",
    )
    _entry(
        account,
        order,
        entry_type=CreditEntry.EntryType.CANCEL_REVERSAL,
        amount="-230.00",
        balance_after="0.00",
    )

    assert check_credit_integrity() == []


def test_integrity_ignores_cod_orders_without_entries() -> None:
    order = _order(payment_method=Order.PaymentMethod.COD)
    CreditAccountFactory(
        user=order.user,
        credit_limit=Decimal("1000.00"),
        balance=Decimal("0.00"),
    )

    assert check_credit_integrity() == []
