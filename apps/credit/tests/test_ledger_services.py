"""Tests for the credit ledger services: charge, reverse, payment and adjustment."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.accounts.models import User
from apps.credit.models import CreditEntry
from apps.credit.services import (
    CreditLimitExceededError,
    CreditNotAvailableError,
    InvalidCreditAmountError,
    charge_order,
    record_adjustment,
    record_payment,
    reverse_order_charge,
)
from apps.credit.tests.factories import CreditAccountFactory
from apps.orders.models import Order
from apps.orders.tests.factories import OrderFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

# A fixed aware moment and its shop-time-zone date, so entry dates never
# depend on when the suite runs.
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
TODAY = date(2026, 11, 7)


def _account(
    user: User,
    *,
    limit: str = "1000.00",
    balance: str = "0.00",
    is_active: bool = True,
):
    return CreditAccountFactory(
        user=user,
        credit_limit=Decimal(limit),
        balance=Decimal(balance),
        is_active=is_active,
    )


def _order(user: User, total: str = "230.00") -> Order:
    """A credit order whose credit part matches the factory's money constraint."""
    amount = Decimal(total)
    fee = Decimal("30.00")
    return OrderFactory(
        user=user,
        payment_method=Order.PaymentMethod.CREDIT,
        item_total=amount - fee,
        delivery_fee=fee,
        grand_total=amount,
        credit_applied=amount,
        amount_due_on_delivery=Decimal("0.00"),
    )


# ---------------------------------------------------------------------------
# Charge
# ---------------------------------------------------------------------------


def test_charge_order_writes_an_order_entry_and_raises_the_balance() -> None:
    user = UserFactory()
    account = _account(user)
    order = _order(user)
    before = account.updated_at

    entry = charge_order(order, actor=UserFactory(), now=NOW)

    assert entry.entry_type == CreditEntry.EntryType.ORDER
    assert entry.amount == Decimal("230.00")
    assert entry.order_id == order.pk
    assert entry.entry_date == TODAY
    account.refresh_from_db()
    assert account.balance == Decimal("230.00")
    assert account.updated_at > before


def test_charge_order_stores_balance_after() -> None:
    user = UserFactory()
    _account(user, balance="100.00")
    order = _order(user)

    entry = charge_order(order, now=NOW)

    assert entry.balance_after == Decimal("330.00")


def test_charge_order_exactly_at_the_available_credit_is_allowed() -> None:
    user = UserFactory()
    account = _account(user, limit="230.00")
    order = _order(user)

    entry = charge_order(order, now=NOW)

    assert entry.amount == Decimal("230.00")
    account.refresh_from_db()
    assert account.balance == Decimal("230.00")


def test_charge_order_one_paisa_above_the_available_credit_is_refused() -> None:
    user = UserFactory()
    _account(user, limit="229.99")
    order = _order(user)

    with pytest.raises(CreditLimitExceededError):
        charge_order(order, now=NOW)


def test_charge_order_refused_writes_nothing() -> None:
    user = UserFactory()
    account = _account(user, limit="100.00")
    order = _order(user)

    with pytest.raises(CreditLimitExceededError):
        charge_order(order, now=NOW)

    assert CreditEntry.objects.filter(account=account).count() == 0
    account.refresh_from_db()
    assert account.balance == Decimal("0.00")


def test_charge_order_refuses_a_user_without_an_account() -> None:
    user = UserFactory()
    order = _order(user)

    with pytest.raises(CreditNotAvailableError):
        charge_order(order, now=NOW)

    assert CreditEntry.objects.count() == 0


def test_charge_order_refuses_a_suspended_account() -> None:
    user = UserFactory()
    _account(user, is_active=False)
    order = _order(user)

    with pytest.raises(CreditNotAvailableError):
        charge_order(order, now=NOW)

    assert CreditEntry.objects.count() == 0


def test_charge_order_charges_the_account_of_the_orders_user() -> None:
    order_user = UserFactory()
    other_user = UserFactory()
    order_account = _account(order_user)
    other_account = _account(other_user)
    order = _order(order_user)

    entry = charge_order(order, now=NOW)

    assert entry.account_id == order_account.pk
    order_account.refresh_from_db()
    other_account.refresh_from_db()
    assert order_account.balance == Decimal("230.00")
    assert other_account.balance == Decimal("0.00")


def test_charge_order_twice_for_the_same_order_returns_the_same_entry() -> None:
    user = UserFactory()
    account = _account(user)
    order = _order(user)

    first = charge_order(order, now=NOW)
    account.refresh_from_db()
    updated = account.updated_at
    second = charge_order(order, now=NOW)

    assert second.pk == first.pk
    assert CreditEntry.objects.filter(account=account).count() == 1
    account.refresh_from_db()
    assert account.balance == Decimal("230.00")
    # The repeat wrote nothing, so the account row was not saved again.
    assert account.updated_at == updated


def test_charge_order_uses_the_orders_credit_applied() -> None:
    user = UserFactory()
    _account(user)
    order = _order(user, total="500.00")

    entry = charge_order(order, now=NOW)

    assert entry.amount == Decimal("500.00")


def test_charge_order_charges_only_the_credit_part_of_a_split_order() -> None:
    user = UserFactory()
    account = _account(user)
    order = OrderFactory(
        user=user,
        payment_method=Order.PaymentMethod.CREDIT,
        credit_applied=Decimal("100.00"),
        amount_due_on_delivery=Decimal("130.00"),
    )

    entry = charge_order(order, now=NOW)

    assert entry.amount == Decimal("100.00")
    account.refresh_from_db()
    assert account.balance == Decimal("100.00")


def test_charge_order_refuses_a_zero_credit_part_and_writes_nothing() -> None:
    user = UserFactory()
    account = _account(user)
    order = OrderFactory(user=user, payment_method=Order.PaymentMethod.COD)

    with pytest.raises(InvalidCreditAmountError):
        charge_order(order, now=NOW)

    assert CreditEntry.objects.filter(account=account).count() == 0
    account.refresh_from_db()
    assert account.balance == Decimal("0.00")


# ---------------------------------------------------------------------------
# Reverse
# ---------------------------------------------------------------------------


def test_reverse_writes_a_cancel_reversal_and_lowers_the_balance() -> None:
    user = UserFactory()
    account = _account(user)
    order = _order(user)
    charge_order(order, now=NOW)
    account.refresh_from_db()
    before = account.updated_at

    entry = reverse_order_charge(order, now=NOW)

    assert entry is not None
    assert entry.entry_type == CreditEntry.EntryType.CANCEL_REVERSAL
    assert entry.amount == Decimal("-230.00")
    assert entry.balance_after == Decimal("0.00")
    account.refresh_from_db()
    assert account.balance == Decimal("0.00")
    assert account.updated_at > before


def test_reverse_twice_writes_one_reversal() -> None:
    user = UserFactory()
    account = _account(user)
    order = _order(user)
    charge_order(order, now=NOW)

    first = reverse_order_charge(order, now=NOW)
    second = reverse_order_charge(order, now=NOW)

    assert first is not None
    assert second is not None
    assert second.pk == first.pk
    assert (
        CreditEntry.objects.filter(
            account=account,
            entry_type=CreditEntry.EntryType.CANCEL_REVERSAL,
        ).count()
        == 1
    )
    account.refresh_from_db()
    assert account.balance == Decimal("0.00")


def test_reverse_of_an_order_without_a_charge_returns_none_and_writes_nothing() -> None:
    user = UserFactory()
    account = _account(user)
    order = _order(user)

    entry = reverse_order_charge(order, now=NOW)

    assert entry is None
    assert CreditEntry.objects.filter(account=account).count() == 0
    account.refresh_from_db()
    assert account.balance == Decimal("0.00")


# ---------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------


def test_record_payment_writes_a_negative_entry_and_lowers_the_balance() -> None:
    user = UserFactory()
    account = _account(user, balance="500.00")
    before = account.updated_at

    entry = record_payment(
        user,
        Decimal("200.00"),
        mode=CreditEntry.PaymentMode.CASH,
        entry_date=TODAY,
        actor=UserFactory(),
        now=NOW,
    )

    assert entry.entry_type == CreditEntry.EntryType.PAYMENT
    assert entry.amount == Decimal("-200.00")
    assert entry.balance_after == Decimal("300.00")
    account.refresh_from_db()
    assert account.balance == Decimal("300.00")
    assert account.updated_at > before


def test_record_payment_stores_mode_date_reference_and_note() -> None:
    user = UserFactory()
    _account(user, balance="500.00")
    paid_on = date(2026, 11, 6)

    entry = record_payment(
        user,
        Decimal("200.00"),
        mode=CreditEntry.PaymentMode.UPI,
        entry_date=paid_on,
        reference="UPI-123456",
        note="Paid by UPI",
        actor=UserFactory(),
        now=NOW,
    )

    assert entry.payment_mode == CreditEntry.PaymentMode.UPI
    assert entry.entry_date == paid_on
    assert entry.reference == "UPI-123456"
    assert entry.note == "Paid by UPI"


def test_record_payment_refuses_zero_and_negative_amounts() -> None:
    user = UserFactory()
    _account(user, balance="500.00")
    amounts = [Decimal("0.00"), Decimal("-10.00")]
    assert amounts

    for amount in amounts:
        with pytest.raises(InvalidCreditAmountError):
            record_payment(
                user,
                amount,
                mode=CreditEntry.PaymentMode.CASH,
                entry_date=TODAY,
                actor=UserFactory(),
                now=NOW,
            )

    assert CreditEntry.objects.count() == 0


def test_record_payment_refuses_more_than_the_balance() -> None:
    user = UserFactory()
    account = _account(user, balance="100.00")

    with pytest.raises(CreditLimitExceededError):
        record_payment(
            user,
            Decimal("100.01"),
            mode=CreditEntry.PaymentMode.CASH,
            entry_date=TODAY,
            actor=UserFactory(),
            now=NOW,
        )

    assert CreditEntry.objects.count() == 0
    account.refresh_from_db()
    assert account.balance == Decimal("100.00")


def test_record_payment_of_exactly_the_balance_is_allowed() -> None:
    user = UserFactory()
    account = _account(user, balance="230.00")

    entry = record_payment(
        user,
        Decimal("230.00"),
        mode=CreditEntry.PaymentMode.BANK,
        entry_date=TODAY,
        actor=UserFactory(),
        now=NOW,
    )

    assert entry.amount == Decimal("-230.00")
    account.refresh_from_db()
    assert account.balance == Decimal("0.00")


def test_record_payment_refuses_a_future_date() -> None:
    user = UserFactory()
    _account(user, balance="500.00")

    with pytest.raises(InvalidCreditAmountError):
        record_payment(
            user,
            Decimal("100.00"),
            mode=CreditEntry.PaymentMode.CASH,
            entry_date=TODAY + timedelta(days=1),
            actor=UserFactory(),
            now=NOW,
        )


def test_record_payment_refuses_an_unknown_mode() -> None:
    user = UserFactory()
    _account(user, balance="500.00")

    with pytest.raises(InvalidCreditAmountError):
        record_payment(
            user,
            Decimal("100.00"),
            mode="CARD",
            entry_date=TODAY,
            actor=UserFactory(),
            now=NOW,
        )


def test_record_payment_refuses_a_user_without_an_account() -> None:
    with pytest.raises(CreditNotAvailableError):
        record_payment(
            UserFactory(),
            Decimal("100.00"),
            mode=CreditEntry.PaymentMode.CASH,
            entry_date=TODAY,
            actor=UserFactory(),
            now=NOW,
        )

    assert CreditEntry.objects.count() == 0


def test_record_payment_records_the_actor() -> None:
    user = UserFactory()
    _account(user, balance="500.00")
    actor = UserFactory()

    entry = record_payment(
        user,
        Decimal("100.00"),
        mode=CreditEntry.PaymentMode.CASH,
        entry_date=TODAY,
        actor=actor,
        now=NOW,
    )

    assert entry.created_by_id == actor.pk


# ---------------------------------------------------------------------------
# Adjustment and the balance_after chain
# ---------------------------------------------------------------------------


def test_record_adjustment_with_a_positive_amount_raises_the_balance() -> None:
    user = UserFactory()
    account = _account(user, balance="100.00")
    before = account.updated_at

    entry = record_adjustment(
        user,
        Decimal("50.00"),
        note="Delivery charge correction",
        actor=UserFactory(),
        now=NOW,
    )

    assert entry.entry_type == CreditEntry.EntryType.ADJUSTMENT
    assert entry.amount == Decimal("50.00")
    assert entry.balance_after == Decimal("150.00")
    account.refresh_from_db()
    assert account.balance == Decimal("150.00")
    assert account.updated_at > before


def test_record_adjustment_with_a_negative_amount_lowers_the_balance() -> None:
    user = UserFactory()
    account = _account(user, balance="100.00")

    entry = record_adjustment(
        user,
        Decimal("-40.00"),
        note="Goodwill discount",
        actor=UserFactory(),
        now=NOW,
    )

    assert entry.amount == Decimal("-40.00")
    account.refresh_from_db()
    assert account.balance == Decimal("60.00")


def test_record_adjustment_needs_a_note() -> None:
    user = UserFactory()
    _account(user, balance="100.00")

    with pytest.raises(InvalidCreditAmountError):
        record_adjustment(
            user,
            Decimal("10.00"),
            note="   ",
            actor=UserFactory(),
            now=NOW,
        )


def test_record_adjustment_refuses_zero() -> None:
    user = UserFactory()
    _account(user, balance="100.00")

    with pytest.raises(InvalidCreditAmountError):
        record_adjustment(
            user,
            Decimal("0.00"),
            note="Nothing to correct",
            actor=UserFactory(),
            now=NOW,
        )


def test_balance_after_chain_follows_the_entries() -> None:
    user = UserFactory()
    account = _account(user)
    order = _order(user)

    charge_order(order, now=NOW)
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
    reverse_order_charge(order, now=NOW)

    # Reading the rows in id order gives the order in which they were written.
    entries = list(CreditEntry.objects.filter(account=account).order_by("id"))
    assert entries
    # A list comprehension builds the balance_after values in one line.
    stored = [entry.balance_after for entry in entries]
    assert stored == [
        Decimal("230.00"),
        Decimal("130.00"),
        Decimal("150.00"),
        Decimal("-80.00"),
    ]
    account.refresh_from_db()
    assert account.balance == Decimal("-80.00")
