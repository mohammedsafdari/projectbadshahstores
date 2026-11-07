"""Concurrency tests for the credit services (Slice 37a, D67).

Every test starts real threads. The data is created and committed in the main
thread first, each thread gets its own database connection and closes it in a
``finally`` block, and the assertions are on final balances and counts, never
on timing or on which thread won.
"""

from __future__ import annotations

import concurrent.futures
import threading
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import connection

from apps.accounts.models import User
from apps.credit.integrity import check_credit_integrity
from apps.credit.models import CreditEntry
from apps.credit.services import (
    CreditError,
    CreditLimitExceededError,
    charge_order,
    record_adjustment,
    record_payment,
    reverse_order_charge,
)
from apps.credit.tests.factories import CreditAccountFactory
from apps.orders.models import Order
from apps.orders.tests.factories import OrderFactory
from tests.factories import UserFactory

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
TODAY = date(2026, 11, 7)


def _run_concurrently(worker, count):
    """Run ``worker(index)`` in ``count`` threads, released together.

    ``threading.Barrier(count)`` makes every thread wait until all of them have
    arrived, so the calls really overlap. A thread gets its own database
    connection, so it cannot see the main thread's uncommitted rows: that is
    why the tests create and commit all data before starting the threads. Each
    thread closes its own connection so the test database can be dropped.

    Returns ("ok", result) or ("error", exception) per thread. Only
    ``CreditError`` becomes an outcome; any other exception fails the test.
    """
    barrier = threading.Barrier(count)

    def _target(index):
        try:
            barrier.wait(timeout=10)
            return ("ok", worker(index))
        except CreditError as exc:
            return ("error", exc)
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(_target, idx) for idx in range(count)]
        return [future.result() for future in futures]


def _account(user: User, *, limit: str = "1000.00", balance: str = "0.00"):
    return CreditAccountFactory(
        user=user,
        credit_limit=Decimal(limit),
        balance=Decimal(balance),
    )


def _order(user: User, total: str) -> Order:
    """An order whose grand total matches the factory's money constraint."""
    amount = Decimal(total)
    fee = Decimal("30.00")
    return OrderFactory(
        user=user,
        item_total=amount - fee,
        delivery_fee=fee,
        grand_total=amount,
        amount_due_on_delivery=amount,
    )


@pytest.mark.django_db(transaction=True)
def test_two_charges_at_once_cannot_exceed_the_limit() -> None:
    user = UserFactory()
    account = _account(user, limit="1000.00")
    order_pks = [
        _order(user, "600.00").pk,
        _order(user, "600.00").pk,
    ]

    def worker(index: int):
        order = Order.objects.get(pk=order_pks[index])
        return charge_order(order, now=NOW)

    outcomes = _run_concurrently(worker, 2)

    ok = [outcome for outcome in outcomes if outcome[0] == "ok"]
    errors = [outcome for outcome in outcomes if outcome[0] == "error"]
    assert len(ok) == 1
    assert len(errors) == 1
    assert isinstance(errors[0][1], CreditLimitExceededError)
    account.refresh_from_db()
    assert account.balance == Decimal("600.00")
    assert CreditEntry.objects.filter(account=account).count() == 1


@pytest.mark.django_db(transaction=True)
def test_two_payments_at_once_cannot_overpay() -> None:
    user = UserFactory()
    account = _account(user, limit="1000.00", balance="500.00")

    def worker(index: int):
        return record_payment(
            user,
            Decimal("400.00"),
            mode=CreditEntry.PaymentMode.CASH,
            entry_date=TODAY,
            actor=None,
            now=NOW,
        )

    outcomes = _run_concurrently(worker, 2)

    ok = [outcome for outcome in outcomes if outcome[0] == "ok"]
    errors = [outcome for outcome in outcomes if outcome[0] == "error"]
    assert len(ok) == 1
    assert len(errors) == 1
    assert isinstance(errors[0][1], CreditLimitExceededError)
    account.refresh_from_db()
    assert account.balance == Decimal("100.00")
    assert CreditEntry.objects.filter(account=account).count() == 1


@pytest.mark.django_db(transaction=True)
def test_charge_and_payment_at_once_keep_the_balance_equal_to_the_entries() -> None:
    user = UserFactory()
    account = _account(user, limit="1000.00")
    # The opening balance is a real ADJUSTMENT row, not a seeded column, so
    # the ledger sum and the cached balance start equal. 300 also lets the
    # payment succeed whichever thread wins the account lock.
    record_adjustment(
        user,
        Decimal("300.00"),
        note="Opening balance",
        actor=None,
        now=NOW,
    )
    order = _order(user, "500.00")

    def worker(index: int):
        if index == 0:
            return charge_order(order, now=NOW)
        return record_payment(
            user,
            Decimal("200.00"),
            mode=CreditEntry.PaymentMode.UPI,
            entry_date=TODAY,
            actor=None,
            now=NOW,
        )

    outcomes = _run_concurrently(worker, 2)

    assert [outcome[0] for outcome in outcomes] == ["ok", "ok"]
    account.refresh_from_db()
    assert account.balance == Decimal("600.00")
    entries = list(CreditEntry.objects.filter(account=account))
    assert entries
    assert sum(entry.amount for entry in entries) == Decimal("600.00")
    assert check_credit_integrity() == []


@pytest.mark.django_db(transaction=True)
def test_two_reversals_at_once_write_one_reversal() -> None:
    user = UserFactory()
    account = _account(user, limit="1000.00")
    order = _order(user, "230.00")
    charge_order(order, now=NOW)

    def worker(index: int):
        return reverse_order_charge(order, now=NOW)

    outcomes = _run_concurrently(worker, 2)

    assert [outcome[0] for outcome in outcomes] == ["ok", "ok"]
    assert outcomes[0][1].pk == outcomes[1][1].pk
    assert (
        CreditEntry.objects.filter(
            account=account,
            entry_type=CreditEntry.EntryType.CANCEL_REVERSAL,
        ).count()
        == 1
    )
    account.refresh_from_db()
    assert account.balance == Decimal("0.00")
    assert CreditEntry.objects.filter(account=account).count() == 2
