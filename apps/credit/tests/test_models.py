"""Model tests for the credit account and the append-only ledger (Slice 37a)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError
from django.db.models import ProtectedError

from apps.core.models import AppendOnlyError
from apps.credit.models import CreditAccount, CreditEntry
from apps.credit.tests.factories import CreditAccountFactory
from apps.orders.models import Order
from apps.orders.tests.factories import OrderFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _entry(
    account: CreditAccount,
    *,
    entry_type: str = CreditEntry.EntryType.ADJUSTMENT,
    amount: str = "10.00",
    order: Order | None = None,
    payment_mode: str = "",
) -> CreditEntry:
    """Create one ledger row directly, for constraint tests."""
    return CreditEntry.objects.create(
        account=account,
        entry_type=entry_type,
        amount=Decimal(amount),
        balance_after=Decimal(amount),
        order=order,
        payment_mode=payment_mode,
        note="test",
        entry_date=date(2026, 11, 7),
    )


def test_account_user_is_one_to_one() -> None:
    user = UserFactory()
    CreditAccountFactory(user=user)

    # OneToOneField means one account per user; a second insert hits the
    # database's unique constraint.
    with pytest.raises(IntegrityError):
        CreditAccountFactory(user=user)


def test_account_limit_cannot_be_negative() -> None:
    with pytest.raises(IntegrityError, match="creditaccount_limit_gte_0"):
        CreditAccountFactory(credit_limit=Decimal("-1.00"))


def test_account_defaults_to_zero_limit_zero_balance_and_active() -> None:
    user = UserFactory()

    account = CreditAccount.objects.create(user=user)

    assert account.credit_limit == Decimal("0.00")
    assert account.balance == Decimal("0.00")
    assert account.is_active is True


def test_entry_amount_cannot_be_zero() -> None:
    account = CreditAccountFactory()

    with pytest.raises(IntegrityError, match="creditentry_amount_not_zero"):
        _entry(account, amount="0.00")


def test_order_entry_must_be_positive() -> None:
    account = CreditAccountFactory()
    order = OrderFactory(user=account.user)

    with pytest.raises(IntegrityError, match="creditentry_order_amount_gt_0"):
        _entry(
            account,
            entry_type=CreditEntry.EntryType.ORDER,
            amount="-1.00",
            order=order,
        )


def test_payment_entry_must_be_negative() -> None:
    account = CreditAccountFactory()

    with pytest.raises(IntegrityError, match="creditentry_payment_amount_lt_0"):
        _entry(
            account,
            entry_type=CreditEntry.EntryType.PAYMENT,
            amount="1.00",
            payment_mode=CreditEntry.PaymentMode.CASH,
        )


def test_cancel_reversal_entry_must_be_negative() -> None:
    account = CreditAccountFactory()
    order = OrderFactory(user=account.user)

    with pytest.raises(IntegrityError, match="creditentry_reversal_amount_lt_0"):
        _entry(
            account,
            entry_type=CreditEntry.EntryType.CANCEL_REVERSAL,
            amount="1.00",
            order=order,
        )


def test_payment_entry_needs_a_payment_mode() -> None:
    account = CreditAccountFactory()

    with pytest.raises(IntegrityError, match="creditentry_payment_has_mode"):
        _entry(
            account,
            entry_type=CreditEntry.EntryType.PAYMENT,
            amount="-1.00",
            payment_mode="",
        )


def test_order_entry_needs_an_order() -> None:
    account = CreditAccountFactory()

    with pytest.raises(IntegrityError, match="creditentry_order_has_order"):
        _entry(
            account,
            entry_type=CreditEntry.EntryType.ORDER,
            amount="1.00",
        )


def test_cancel_reversal_entry_needs_an_order() -> None:
    account = CreditAccountFactory()

    with pytest.raises(IntegrityError, match="creditentry_order_has_order"):
        _entry(
            account,
            entry_type=CreditEntry.EntryType.CANCEL_REVERSAL,
            amount="-1.00",
        )


def test_only_one_order_entry_per_order() -> None:
    account = CreditAccountFactory()
    order = OrderFactory(user=account.user)
    _entry(account, entry_type=CreditEntry.EntryType.ORDER, amount="1.00", order=order)

    with pytest.raises(IntegrityError, match="creditentry_unique_order_charge"):
        _entry(
            account,
            entry_type=CreditEntry.EntryType.ORDER,
            amount="1.00",
            order=order,
        )


def test_only_one_cancel_reversal_per_order() -> None:
    account = CreditAccountFactory()
    order = OrderFactory(user=account.user)
    _entry(
        account,
        entry_type=CreditEntry.EntryType.CANCEL_REVERSAL,
        amount="-1.00",
        order=order,
    )

    with pytest.raises(IntegrityError, match="creditentry_unique_order_reversal"):
        _entry(
            account,
            entry_type=CreditEntry.EntryType.CANCEL_REVERSAL,
            amount="-1.00",
            order=order,
        )


def test_entry_cannot_be_updated() -> None:
    account = CreditAccountFactory()
    entry = _entry(account)
    entry.note = "changed"

    with pytest.raises(AppendOnlyError):
        entry.save()


def test_entry_cannot_be_deleted() -> None:
    account = CreditAccountFactory()
    entry = _entry(account)

    with pytest.raises(AppendOnlyError):
        entry.delete()


def test_entry_queryset_update_and_delete_are_blocked() -> None:
    account = CreditAccountFactory()
    _entry(account)

    # Queryset update and delete skip save(), so they need their own guard.
    with pytest.raises(AppendOnlyError):
        CreditEntry.objects.all().update(note="changed")
    with pytest.raises(AppendOnlyError):
        CreditEntry.objects.all().delete()


def test_an_account_with_entries_cannot_be_deleted() -> None:
    account = CreditAccountFactory()
    _entry(account)

    with pytest.raises(ProtectedError):
        account.delete()


def test_a_user_with_an_account_cannot_be_deleted() -> None:
    account = CreditAccountFactory()

    with pytest.raises(ProtectedError):
        account.user.delete()
