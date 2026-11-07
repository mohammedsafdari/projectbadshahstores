"""Tests for the read-only credit integrity report (Slice 37a)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import connection

from apps.credit.integrity import check_credit_integrity
from apps.credit.models import CreditAccount, CreditEntry
from apps.credit.services import (
    charge_order,
    record_adjustment,
    record_payment,
)
from apps.credit.tests.factories import CreditAccountFactory
from apps.orders.tests.factories import OrderFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
TODAY = date(2026, 11, 7)


def _charge(user, total: str = "230.00"):
    """Create a matching order and charge it to the user's account."""
    amount = Decimal(total)
    order = OrderFactory(
        user=user,
        item_total=amount - Decimal("30.00"),
        delivery_fee=Decimal("30.00"),
        grand_total=amount,
        amount_due_on_delivery=amount,
    )
    return charge_order(order, now=NOW)


def test_integrity_is_clean_after_normal_use() -> None:
    user = UserFactory()
    CreditAccountFactory(user=user, credit_limit=Decimal("1000.00"))

    _charge(user)
    record_payment(
        user,
        Decimal("100.00"),
        mode=CreditEntry.PaymentMode.CASH,
        entry_date=TODAY,
        actor=UserFactory(),
        now=NOW,
    )
    record_adjustment(
        user,
        Decimal("20.00"),
        note="Rounding correction",
        actor=UserFactory(),
        now=NOW,
    )

    assert check_credit_integrity() == []


def test_integrity_reports_a_balance_that_differs_from_the_entries() -> None:
    user = UserFactory()
    account = CreditAccountFactory(user=user, credit_limit=Decimal("1000.00"))
    _charge(user)

    # The account model allows a queryset update; only the ledger is
    # append-only. This simulates a bug that broke the cached balance.
    CreditAccount.objects.filter(pk=account.pk).update(balance=Decimal("999.00"))

    problems = check_credit_integrity()

    assert len(problems) == 1
    assert problems[0].account_id == account.pk
    assert problems[0].code == "balance_mismatch"


def test_integrity_reports_a_broken_balance_after_chain() -> None:
    user = UserFactory()
    account = CreditAccountFactory(user=user, credit_limit=Decimal("1000.00"))
    entry = _charge(user)

    # Raw SQL bypasses the model's append-only save() guard.
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE credit_creditentry SET balance_after = %s WHERE id = %s",
            [Decimal("999.00"), entry.pk],
        )

    problems = check_credit_integrity()

    assert len(problems) == 1
    assert problems[0].account_id == account.pk
    assert problems[0].code == "chain_broken"


def test_integrity_reports_an_order_entry_that_differs_from_the_order_total() -> None:
    user = UserFactory()
    account = CreditAccountFactory(
        user=user,
        credit_limit=Decimal("1000.00"),
        balance=Decimal("100.00"),
    )
    order = OrderFactory(user=user)
    # A wrong amount written straight into the ledger: the cached balance and
    # the chain still agree, but the order's frozen total does not.
    CreditEntry.objects.create(
        account=account,
        entry_type=CreditEntry.EntryType.ORDER,
        amount=Decimal("100.00"),
        balance_after=Decimal("100.00"),
        order=order,
        entry_date=TODAY,
    )

    problems = check_credit_integrity()

    assert len(problems) == 1
    assert problems[0].account_id == account.pk
    assert problems[0].code == "order_amount_mismatch"


def test_integrity_ignores_accounts_without_entries() -> None:
    account = CreditAccountFactory(
        credit_limit=Decimal("1000.00"),
        balance=Decimal("0.00"),
    )

    problems = check_credit_integrity()

    assert problems == []
    assert not [item for item in problems if item.account_id == account.pk]
