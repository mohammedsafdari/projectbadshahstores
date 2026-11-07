"""Concurrency tests for credit checkout and cancellation (D68).

Every test starts real threads. The data is created and committed first, each
thread gets its own database connection and closes it in a ``finally`` block,
and the assertions are on final counts and the ledger, never on timing or on
which thread won.
"""

from __future__ import annotations

import concurrent.futures
import threading
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import connection

from apps.accounts.models import User
from apps.credit.integrity import check_credit_integrity
from apps.credit.models import CreditAccount, CreditEntry
from apps.orders.models import Order
from apps.orders.services import (
    OrderError,
    cancel_order_by_customer,
    transition_order,
)
from apps.orders.tests.checkout_helpers import (
    NOW,
    address,
    clear_settings,
    credit_user,
    fill_cart,
    place_credit,
    reset_settings,
    serve,
    variant,
)
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before any thread runs."""
    reset_settings()
    yield
    clear_settings()


def _run_concurrently(worker, count):
    """Run ``worker(index)`` in ``count`` threads that start together.

    ``threading.Barrier`` releases all threads at once. Each thread closes its
    own connection in a ``finally`` block, so the test database can be dropped.
    An ``OrderError`` becomes an ("error", exception) outcome; anything else
    propagates and fails the test.
    """
    barrier = threading.Barrier(count)

    def target(index):
        try:
            barrier.wait(timeout=10)
            return ("ok", worker(index))
        except OrderError as exc:
            return ("error", exc)
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(target, index) for index in range(count)]
        return [future.result() for future in futures]


def _ledger_total(account: CreditAccount) -> Decimal:
    """The sum of every entry of one account, in Decimal."""
    return sum(
        (entry.amount for entry in CreditEntry.objects.filter(account=account)),
        Decimal("0.00"),
    )


def test_credit_checkout_and_cancel_at_once_keep_the_balance_equal_to_the_entries() -> None:
    user = credit_user(limit="1000.00")
    serve()
    address_row = address(user)
    row = variant()
    fill_cart(user, row)
    cancelled_order = place_credit(user, address_row).order
    # The cart was cleared by the first order, so fill it again for order B.
    fill_cart(user, row)

    user_pk = user.pk
    cancelled_pk = cancelled_order.pk
    group = uuid4()

    def worker(index: int):
        thread_user = User.objects.get(pk=user_pk)
        if index == 0:
            return cancel_order_by_customer(thread_user, cancelled_pk, now=NOW)
        return place_credit(thread_user, address_row, group=group)

    outcomes = _run_concurrently(worker, count=2)

    assert all(outcome[0] == "ok" for outcome in outcomes), outcomes
    account = CreditAccount.objects.get(user_id=user_pk)
    assert check_credit_integrity() == []
    assert account.balance == _ledger_total(account)
    assert account.balance == Decimal("230.00")
    assert Order.objects.get(pk=cancelled_pk).status == Order.Status.CANCELLED


def test_same_group_submitted_twice_at_once_charges_once() -> None:
    user = credit_user(limit="1000.00")
    serve()
    address_row = address(user)
    fill_cart(user, variant())
    user_pk = user.pk
    group = uuid4()

    def worker(index: int):
        thread_user = User.objects.get(pk=user_pk)
        return place_credit(thread_user, address_row, group=group)

    outcomes = _run_concurrently(worker, count=2)

    assert all(outcome[0] == "ok" for outcome in outcomes), outcomes
    created = sorted(outcome[1].created for outcome in outcomes)
    assert created == [False, True]
    order = Order.objects.get()
    assert CreditEntry.objects.filter(order=order).count() == 1
    assert CreditAccount.objects.get(user_id=user_pk).balance == Decimal("230.00")


def test_cancel_twice_at_once_reverses_once() -> None:
    user = credit_user(limit="1000.00")
    serve()
    address_row = address(user)
    fill_cart(user, variant())
    order = place_credit(user, address_row).order
    order_pk = order.pk
    staff = UserFactory()
    staff_pk = staff.pk

    def worker(index: int):
        actor = User.objects.get(pk=staff_pk)
        return transition_order(
            order_pk, Order.Status.CANCELLED, actor=actor, reason="Race", now=NOW
        )

    outcomes = _run_concurrently(worker, count=2)

    ok = [outcome for outcome in outcomes if outcome[0] == "ok"]
    errors = [outcome for outcome in outcomes if outcome[0] == "error"]
    assert len(ok) == 1
    assert len(errors) == 1
    assert (
        CreditEntry.objects.filter(
            order_id=order_pk,
            entry_type=CreditEntry.EntryType.CANCEL_REVERSAL,
        ).count()
        == 1
    )
    assert CreditAccount.objects.get(user_id=user.pk).balance == Decimal("0.00")
