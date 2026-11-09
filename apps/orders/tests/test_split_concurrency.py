"""Concurrency tests for split orders (D74).

Every test starts real threads. The data is created and committed first, each
thread gets its own database connection and closes it in a ``finally`` block,
and the assertions are on final counts and the ledger, never on timing or on
which thread won. This copies the pattern of ``test_concurrency_credit.py``.
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
from apps.credit.services import (
    CreditError,
    charge_order,
    record_adjustment,
    record_payment,
)
from apps.orders.models import Order
from apps.orders.services import OrderError, transition_order
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
from apps.orders.tests.factories import OrderFactory
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
    A business refusal (``OrderError`` or ``CreditError``) becomes an
    ("error", exception) outcome; anything else propagates and fails the test.
    """
    barrier = threading.Barrier(count)

    def target(index):
        try:
            barrier.wait(timeout=10)
            return ("ok", worker(index))
        except (OrderError, CreditError) as exc:
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


def test_two_orders_racing_for_the_same_credit_never_charge_more_than_the_limit() -> None:
    # Two live credit orders each want the whole 230.00 limit. The user row is
    # locked first, so the second charge sees the used-up credit and refuses.
    user = credit_user(limit="230.00")
    first = OrderFactory(user=user, payment_method=Order.PaymentMethod.CREDIT)
    second = OrderFactory(user=user, payment_method=Order.PaymentMethod.CREDIT)
    order_pks = (first.pk, second.pk)
    user_pk = user.pk

    def worker(index: int):
        order = Order.objects.get(pk=order_pks[index])
        return charge_order(order, now=NOW)

    outcomes = _run_concurrently(worker, count=2)

    ok = [outcome for outcome in outcomes if outcome[0] == "ok"]
    errors = [outcome for outcome in outcomes if outcome[0] == "error"]
    assert len(ok) == 1, outcomes
    assert len(errors) == 1, outcomes
    account = CreditAccount.objects.get(user_id=user_pk)
    assert account.balance == Decimal("230.00")
    assert account.balance == _ledger_total(account)
    # Exactly one of the two orders got its ORDER entry.
    assert (
        CreditEntry.objects.filter(
            entry_type=CreditEntry.EntryType.ORDER,
        ).count()
        == 1
    )


def test_a_payment_recorded_while_an_order_is_placed_keeps_the_ledger_chain_intact() -> None:
    # A real opening row of 100.00 (not just a cached balance) keeps the
    # integrity check clean and lets the payment succeed even if it wins the
    # race: 100.00 - 100.00 = 0.00, then the 230.00 charge follows.
    user = credit_user(limit="1000.00")
    record_adjustment(user, Decimal("100.00"), note="Opening balance", actor=None, now=NOW)
    serve()
    address_row = address(user)
    fill_cart(user, variant())
    user_pk = user.pk

    def worker(index: int):
        thread_user = User.objects.get(pk=user_pk)
        if index == 0:
            return place_credit(thread_user, address_row)
        return record_payment(
            thread_user,
            Decimal("100.00"),
            mode=CreditEntry.PaymentMode.CASH,
            entry_date=NOW.date(),
            actor=None,
            now=NOW,
        )

    outcomes = _run_concurrently(worker, count=2)

    assert all(outcome[0] == "ok" for outcome in outcomes), outcomes
    account = CreditAccount.objects.get(user_id=user_pk)
    # Both thread orders lock the user row first, so either order of the two
    # writes ends at the same balance and one valid chain.
    assert account.balance == Decimal("230.00")
    assert account.balance == _ledger_total(account)
    assert check_credit_integrity() == []


def test_two_identical_split_submissions_create_one_order_and_one_charge() -> None:
    user = credit_user(limit="100.00")
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
    assert order.credit_applied == Decimal("100.00")
    assert CreditEntry.objects.filter(order=order).count() == 1
    assert CreditAccount.objects.get(user_id=user_pk).balance == Decimal("100.00")


def test_a_cancel_racing_a_second_cancel_returns_the_credit_part_once() -> None:
    user = credit_user(limit="100.00")
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
    assert len(ok) == 1, outcomes
    assert len(errors) == 1, outcomes
    reversals = CreditEntry.objects.filter(
        order_id=order_pk,
        entry_type=CreditEntry.EntryType.CANCEL_REVERSAL,
    )
    assert reversals.count() == 1
    assert reversals.get().amount == Decimal("-100.00")
    assert CreditAccount.objects.get(user=user).balance == Decimal("0.00")
