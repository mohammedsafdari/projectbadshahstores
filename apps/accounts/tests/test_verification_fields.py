"""Tests for the two verification timestamps and the readiness rule (D71)."""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.accounts.models import User
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"


def test_new_user_has_no_verified_times() -> None:
    user = User.objects.create_user(phone=PHONE)

    assert user.phone_verified_at is None
    assert user.email_verified_at is None
    assert user.is_ready_for_delivery is False


def test_user_without_email_is_ready_when_the_phone_is_verified() -> None:
    user = User.objects.create_user(phone=PHONE)
    user.phone_verified_at = timezone.now()
    user.save(update_fields=["phone_verified_at"])

    # An OTP-only customer has no email, so the phone is the only proof needed.
    assert user.is_ready_for_delivery is True


def test_user_with_email_needs_the_email_verified_too() -> None:
    user = User.objects.create_user(phone=PHONE, email="asha@example.com")
    user.phone_verified_at = timezone.now()
    user.save(update_fields=["phone_verified_at"])

    assert user.is_ready_for_delivery is False

    user.email_verified_at = timezone.now()
    user.save(update_fields=["email_verified_at"])

    assert user.is_ready_for_delivery is True


def test_unverified_phone_is_never_ready() -> None:
    user = User.objects.create_user(phone=PHONE, email="asha@example.com")
    user.email_verified_at = timezone.now()
    user.save(update_fields=["email_verified_at"])

    # A verified email without a verified phone is still not enough.
    assert user.is_ready_for_delivery is False


def test_factory_users_are_verified_by_default() -> None:
    user = UserFactory()

    assert user.phone_verified_at is not None
    assert user.email_verified_at is not None
    assert user.is_ready_for_delivery is True
