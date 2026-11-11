"""The credit-limit verification guard and the admin state (D71, D79)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.credit.models import CreditAccount
from apps.credit.services import CreditCustomerNotVerifiedError, set_credit_limit
from apps.credit.tests.factories import CreditAccountFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _superuser() -> User:
    return User.objects.create_superuser(phone="+919876543210", password="ok")


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def test_limit_above_zero_refused_for_unverified_phone() -> None:
    user = UserFactory(phone_verified_at=None, email_verified_at=timezone.now())

    with pytest.raises(CreditCustomerNotVerifiedError):
        set_credit_limit(user, Decimal("500.00"), actor=UserFactory())

    assert not CreditAccount.objects.filter(user=user).exists()


def test_limit_above_zero_refused_for_unverified_email() -> None:
    user = UserFactory(phone_verified_at=timezone.now(), email_verified_at=None)

    with pytest.raises(CreditCustomerNotVerifiedError):
        set_credit_limit(user, Decimal("500.00"), actor=UserFactory())

    assert not CreditAccount.objects.filter(user=user).exists()


def test_limit_allowed_for_verified_customer() -> None:
    user = UserFactory()

    account = set_credit_limit(user, Decimal("500.00"), actor=UserFactory())

    assert account.credit_limit == Decimal("500.00")
    assert account.balance == Decimal("0.00")


def test_limit_zero_allowed_for_unverified_customer() -> None:
    user = UserFactory(phone_verified_at=None, email_verified_at=None)

    account = set_credit_limit(user, Decimal("0.00"), actor=UserFactory())

    assert account.credit_limit == Decimal("0.00")
    assert CreditAccount.objects.filter(user=user).exists()


def test_credit_admin_shows_verification_state() -> None:
    unverified = UserFactory(phone_verified_at=None, email_verified_at=None)
    unverified_account = CreditAccountFactory(
        user=unverified,
        credit_limit=Decimal("0.00"),
    )
    verified_account = CreditAccountFactory(user=UserFactory())
    client = _client(_superuser())

    unverified_html = client.get(
        reverse("admin:credit_creditaccount_change", args=[unverified_account.pk])
    ).content.decode()
    verified_html = client.get(
        reverse("admin:credit_creditaccount_change", args=[verified_account.pk])
    ).content.decode()

    assert "Not verified" in unverified_html
    assert "phone" in unverified_html
    assert "Verified" in verified_html
    assert "Not verified" not in verified_html
