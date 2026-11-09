"""Integrity findings for split orders (D74).

Each test builds the smallest ledger that isolates one finding, using the real
``CreditEntry`` rows the services would write. ``check_credit_integrity`` is
read-only, so nothing here changes a row.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.credit.integrity import check_credit_integrity
from apps.credit.models import CreditAccount, CreditEntry
from apps.credit.tests.factories import CreditAccountFactory
from apps.orders.models import Order
from apps.orders.services import transition_order
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

pytestmark = pytest.mark.django_db

TODAY = date(2026, 11, 7)


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before every test."""
    reset_settings()
    yield
    clear_settings()


def _split_order(*, credit: str = "100.00", status: str = Order.Status.CONFIRMED) -> Order:
    """A 230.00 credit order with ``credit`` frozen on the khata."""
    return OrderFactory(
        payment_method=Order.PaymentMethod.CREDIT,
        status=status,
        credit_applied=Decimal(credit),
        amount_due_on_delivery=Decimal("230.00") - Decimal(credit),
    )


def _entry(
    account: CreditAccount,
    order: Order,
    *,
    amount: str,
    balance_after: str,
    entry_type: str = CreditEntry.EntryType.ORDER,
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


def test_charge_equal_to_credit_applied_is_clean() -> None:
    order = _split_order()
    account = CreditAccountFactory(
        user=order.user,
        credit_limit=Decimal("1000.00"),
        balance=Decimal("100.00"),
    )
    _entry(account, order, amount="100.00", balance_after="100.00")

    assert check_credit_integrity() == []


def test_charge_equal_to_grand_total_on_a_split_order_is_order_amount_mismatch() -> None:
    order = _split_order()
    account = CreditAccountFactory(
        user=order.user,
        credit_limit=Decimal("1000.00"),
        balance=Decimal("230.00"),
    )
    _entry(account, order, amount="230.00", balance_after="230.00")

    problems = check_credit_integrity()

    assert {problem.code for problem in problems} == {"order_amount_mismatch"}
    assert {problem.account_id for problem in problems} == {account.pk}


def test_charge_below_credit_applied_is_order_amount_mismatch() -> None:
    order = _split_order()
    account = CreditAccountFactory(
        user=order.user,
        credit_limit=Decimal("1000.00"),
        balance=Decimal("80.00"),
    )
    _entry(account, order, amount="80.00", balance_after="80.00")

    problems = check_credit_integrity()

    assert {problem.code for problem in problems} == {"order_amount_mismatch"}
    assert {problem.account_id for problem in problems} == {account.pk}


def test_split_order_without_a_charge_is_credit_order_without_charge() -> None:
    order = _split_order()
    account = CreditAccountFactory(user=order.user, credit_limit=Decimal("1000.00"))

    problems = check_credit_integrity()

    assert {problem.code for problem in problems} == {"credit_order_without_charge"}
    assert {problem.account_id for problem in problems} == {account.pk}


def test_cod_order_with_a_charge_is_charge_on_non_credit_order() -> None:
    order = OrderFactory(payment_method=Order.PaymentMethod.COD)
    account = CreditAccountFactory(
        user=order.user,
        credit_limit=Decimal("1000.00"),
        balance=Decimal("230.00"),
    )
    _entry(account, order, amount="230.00", balance_after="230.00")

    codes = {problem.code for problem in check_credit_integrity()}

    # A COD order puts 0.00 on the khata, so the same row is also an amount
    # mismatch. Both findings describe the one bad charge.
    assert codes == {"order_amount_mismatch", "charge_on_non_credit_order"}


def test_cancelled_split_order_with_its_reversal_is_clean() -> None:
    user = credit_user(limit="100.00")
    serve()
    address_row = address(user)
    fill_cart(user, variant())
    order = place_credit(user, address_row).order
    staff = UserFactory()

    transition_order(
        order.pk,
        Order.Status.CANCELLED,
        actor=staff,
        reason="Out of stock",
        now=NOW,
    )

    assert check_credit_integrity() == []
