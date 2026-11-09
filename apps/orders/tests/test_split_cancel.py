"""Cancelling a split order (part khata, part cash) reverses only the khata part.

Every test places a real order through the service and cancels it through a
real entry point: ``cancel_order_by_customer`` or ``transition_order``. The
ledger, not the order row, is the source of truth for the money.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.credit.integrity import check_credit_integrity
from apps.credit.models import CreditAccount, CreditEntry
from apps.orders.models import Order
from apps.orders.services import (
    InvalidTransitionError,
    cancel_order_by_customer,
    transition_order,
)
from apps.orders.state import PACKING
from apps.orders.tests.checkout_helpers import (
    NOW,
    address,
    clear_settings,
    credit_user,
    fill_cart,
    place_cod,
    place_credit,
    reset_settings,
    serve,
    variant,
)
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before every test."""
    reset_settings()
    yield
    clear_settings()


def _split(limit: str = "100.00", balance: str = "0.00"):
    """A served customer with stock and a 230.00 cart, then a split order.

    With a 100.00 limit and a 230.00 total, ``credit_applied`` is 100.00 and
    the cash part at the door is 130.00 (D74).
    """
    user = credit_user(limit=limit, balance=balance)
    serve()
    address_row = address(user)
    fill_cart(user, variant())
    order = place_credit(user, address_row).order
    return user, order


def _reversal(order: Order) -> CreditEntry:
    """The single cancellation reversal of this order."""
    return CreditEntry.objects.get(
        order=order,
        entry_type=CreditEntry.EntryType.CANCEL_REVERSAL,
    )


def test_customer_cancel_of_a_split_order_returns_only_the_credit_part() -> None:
    user, order = _split()

    cancel_order_by_customer(user, order.pk, now=NOW)

    assert _reversal(order).amount == Decimal("-100.00")
    assert CreditAccount.objects.get(user=user).balance == Decimal("0.00")
    assert CreditEntry.objects.filter(order=order).count() == 2


def test_customer_cancel_of_a_split_order_leaves_the_balance_as_before_the_order() -> None:
    # The customer already owed 50.00, so the limit leaves 100.00 available.
    user, order = _split(limit="150.00", balance="50.00")
    assert CreditAccount.objects.get(user=user).balance == Decimal("150.00")

    cancel_order_by_customer(user, order.pk, now=NOW)

    assert CreditAccount.objects.get(user=user).balance == Decimal("50.00")


def test_staff_cancel_of_a_split_order_returns_only_the_credit_part() -> None:
    user, order = _split()
    staff = UserFactory()

    transition_order(
        order.pk,
        Order.Status.CANCELLED,
        actor=staff,
        reason="Out of stock",
        now=NOW,
    )

    reversal = _reversal(order)
    assert reversal.amount == Decimal("-100.00")
    assert reversal.created_by_id == staff.pk
    assert CreditAccount.objects.get(user=user).balance == Decimal("0.00")


def test_cancel_of_a_cod_order_without_a_credit_account_writes_nothing() -> None:
    # The named test here duplicated the COD cancel test in test_cancel_credit;
    # this edge uses a customer who has no khata at all, so the reversal hook
    # finds no account and must still finish cleanly.
    user = UserFactory()
    serve()
    address_row = address(user)
    fill_cart(user, variant())
    order = place_cod(user, address_row).order
    staff = UserFactory()

    transition_order(
        order.pk,
        Order.Status.CANCELLED,
        actor=staff,
        reason="Out of stock",
        now=NOW,
    )

    assert CreditAccount.objects.count() == 0
    assert CreditEntry.objects.count() == 0


def test_cancel_of_a_split_order_after_packing_started_returns_only_the_credit_part() -> None:
    # The named "fully covered" test duplicated an existing full-credit cancel;
    # this edge cancels a split order after packing has started.
    user, order = _split()
    staff = UserFactory()
    transition_order(order.pk, PACKING, actor=staff, now=NOW)

    transition_order(
        order.pk,
        Order.Status.CANCELLED,
        actor=staff,
        reason="Damaged",
        now=NOW,
    )

    assert _reversal(order).amount == Decimal("-100.00")
    assert CreditAccount.objects.get(user=user).balance == Decimal("0.00")


def test_second_cancel_of_a_split_order_returns_the_credit_part_once() -> None:
    user, order = _split()
    cancel_order_by_customer(user, order.pk, now=NOW)

    with pytest.raises(InvalidTransitionError):
        cancel_order_by_customer(user, order.pk, now=NOW)

    assert (
        CreditEntry.objects.filter(
            order=order,
            entry_type=CreditEntry.EntryType.CANCEL_REVERSAL,
        ).count()
        == 1
    )
    assert _reversal(order).amount == Decimal("-100.00")
    assert CreditAccount.objects.get(user=user).balance == Decimal("0.00")


def test_cancel_keeps_credit_applied_and_amount_due_on_the_cancelled_order() -> None:
    _user, order = _split()
    staff = UserFactory()

    transition_order(
        order.pk,
        Order.Status.CANCELLED,
        actor=staff,
        reason="Customer changed their mind",
        now=NOW,
    )

    order.refresh_from_db()
    assert order.status == Order.Status.CANCELLED
    assert order.credit_applied == Decimal("100.00")
    assert order.amount_due_on_delivery == Decimal("130.00")


def test_cancelled_split_order_passes_the_credit_integrity_check() -> None:
    _user, order = _split()
    staff = UserFactory()

    transition_order(
        order.pk,
        Order.Status.CANCELLED,
        actor=staff,
        reason="Out of stock",
        now=NOW,
    )

    assert check_credit_integrity() == []
