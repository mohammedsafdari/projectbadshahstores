"""Tests for the credit-account limit, status and availability services (Slice 37a)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.core.models import AuditLog
from apps.credit.models import CreditAccount
from apps.credit.services import (
    CREDIT_ACTIVE_ACTION,
    CREDIT_LIMIT_ACTION,
    InvalidCreditAmountError,
    available_credit,
    set_credit_active,
    set_credit_limit,
)
from apps.credit.tests.factories import CreditAccountFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_set_limit_creates_the_account() -> None:
    user = UserFactory()
    actor = UserFactory()

    account = set_credit_limit(user, Decimal("500.00"), actor=actor)

    assert account.pk is not None
    assert account.user == user
    assert account.credit_limit == Decimal("500.00")
    assert account.balance == Decimal("0.00")
    assert account.is_active is True


def test_set_limit_changes_an_existing_limit() -> None:
    account = CreditAccountFactory(credit_limit=Decimal("1000.00"))
    before = account.updated_at

    set_credit_limit(account.user, Decimal("1500.00"), actor=UserFactory())

    account.refresh_from_db()
    assert account.credit_limit == Decimal("1500.00")
    assert account.updated_at > before


def test_set_limit_refuses_a_negative_amount() -> None:
    user = UserFactory()

    with pytest.raises(InvalidCreditAmountError):
        set_credit_limit(user, Decimal("-1.00"), actor=UserFactory())

    assert not CreditAccount.objects.filter(user=user).exists()


def test_set_limit_refuses_more_than_two_decimals() -> None:
    user = UserFactory()

    with pytest.raises(InvalidCreditAmountError):
        set_credit_limit(user, Decimal("10.005"), actor=UserFactory())

    assert not CreditAccount.objects.filter(user=user).exists()


def test_set_limit_writes_an_audit_row_with_old_and_new_values() -> None:
    account = CreditAccountFactory(credit_limit=Decimal("1000.00"))
    actor = UserFactory()

    set_credit_limit(account.user, Decimal("1500.00"), actor=actor)

    audit = AuditLog.objects.get(action=CREDIT_LIMIT_ACTION)
    assert audit.actor_id == actor.pk
    assert audit.target_type == "credit.creditaccount"
    assert audit.target_id == str(account.pk)
    assert audit.changes == {"credit_limit": ["1000.00", "1500.00"]}


def test_set_limit_to_the_same_value_writes_no_audit_row() -> None:
    account = CreditAccountFactory(credit_limit=Decimal("1000.00"))
    before = account.updated_at

    set_credit_limit(account.user, Decimal("1000.00"), actor=UserFactory())

    account.refresh_from_db()
    assert not AuditLog.objects.exists()
    # Nothing changed, so the row was not saved and updated_at did not move.
    assert account.updated_at == before


def test_lowering_the_limit_below_the_balance_is_allowed() -> None:
    account = CreditAccountFactory(
        credit_limit=Decimal("1000.00"),
        balance=Decimal("800.00"),
    )

    set_credit_limit(account.user, Decimal("500.00"), actor=UserFactory())

    account.refresh_from_db()
    assert account.credit_limit == Decimal("500.00")
    assert account.balance == Decimal("800.00")
    assert available_credit(account.user) == Decimal("0.00")


def test_set_active_false_suspends_credit() -> None:
    account = CreditAccountFactory(is_active=True)

    set_credit_active(account.user, False, actor=UserFactory())

    account.refresh_from_db()
    assert account.is_active is False
    assert available_credit(account.user) == Decimal("0.00")


def test_set_active_writes_an_audit_row() -> None:
    account = CreditAccountFactory(is_active=True)
    actor = UserFactory()
    before = account.updated_at

    set_credit_active(account.user, False, actor=actor)

    audit = AuditLog.objects.get(action=CREDIT_ACTIVE_ACTION)
    assert audit.actor_id == actor.pk
    assert audit.changes == {"is_active": [True, False]}
    account.refresh_from_db()
    assert account.updated_at > before


def test_available_credit_is_limit_minus_balance() -> None:
    account = CreditAccountFactory(
        credit_limit=Decimal("1000.00"),
        balance=Decimal("250.00"),
    )

    assert available_credit(account.user) == Decimal("750.00")


def test_available_credit_is_zero_when_the_balance_is_above_the_limit() -> None:
    account = CreditAccountFactory(
        credit_limit=Decimal("1000.00"),
        balance=Decimal("1200.00"),
    )

    assert available_credit(account.user) == Decimal("0.00")


def test_available_credit_is_zero_without_an_account() -> None:
    assert available_credit(UserFactory()) == Decimal("0.00")


def test_available_credit_is_zero_when_suspended() -> None:
    account = CreditAccountFactory(
        credit_limit=Decimal("1000.00"),
        balance=Decimal("200.00"),
        is_active=False,
    )

    assert available_credit(account.user) == Decimal("0.00")
