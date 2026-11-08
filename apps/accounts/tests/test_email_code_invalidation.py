"""Tests for closing pending email codes on an email change (D73)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib import admin
from django.test import RequestFactory
from django.utils import timezone

from apps.accounts.admin import UserAdmin
from apps.accounts.models import OtpChallenge, User
from apps.accounts.otp import request_code
from apps.accounts.verification import (
    invalidate_email_codes,
    request_email_code,
    request_phone_code,
)
from apps.notifications.tests.recording import RecordingSmsBackend
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
OTHER_PHONE = "+919876543211"

_request_factory = RequestFactory()


@pytest.fixture(autouse=True)
def _recording_sms(settings):
    settings.SMS_BACKEND = "apps.notifications.tests.recording.RecordingSmsBackend"
    RecordingSmsBackend.codes.clear()
    yield
    RecordingSmsBackend.codes.clear()


def _customer(*, phone: str = PHONE, email: str = "asha@example.com") -> User:
    return User.objects.create_user(phone=phone, email=email)


def _unused(*purposes: str):
    return OtpChallenge.objects.filter(
        purpose__in=purposes,
        consumed_at__isnull=True,
    )


def test_invalidate_closes_unused_email_codes() -> None:
    user = _customer()
    request_email_code(user)

    closed = invalidate_email_codes(user)

    assert closed == 1
    assert _unused(OtpChallenge.Purpose.EMAIL_VERIFY).count() == 0
    challenge = OtpChallenge.objects.get(purpose=OtpChallenge.Purpose.EMAIL_VERIFY)
    assert challenge.consumed_at is not None


def test_invalidate_leaves_phone_and_login_codes_alone() -> None:
    user = _customer()
    request_phone_code(user)
    request_code(user.phone)
    request_email_code(user)

    invalidate_email_codes(user)

    assert _unused(OtpChallenge.Purpose.PHONE_VERIFY).count() == 1
    assert _unused(OtpChallenge.Purpose.LOGIN).count() == 1
    assert _unused(OtpChallenge.Purpose.EMAIL_VERIFY).count() == 0


def test_invalidate_leaves_other_accounts_codes_alone() -> None:
    user = _customer()
    other = _customer(phone=OTHER_PHONE, email="other@example.com")
    request_email_code(user)
    request_email_code(other)

    invalidate_email_codes(user)

    assert _unused(OtpChallenge.Purpose.EMAIL_VERIFY).filter(phone=PHONE).count() == 0
    assert _unused(OtpChallenge.Purpose.EMAIL_VERIFY).filter(phone=OTHER_PHONE).count() == 1


def test_admin_email_change_closes_pending_email_codes() -> None:
    user = UserFactory(email="asha@example.com")
    request_email_code(user)
    admin_user = User.objects.create_superuser(phone="9876543210", password="ok")
    request = _request_factory.get("/")
    request.user = admin_user
    user_admin = UserAdmin(User, admin.site)

    user_admin.save_model(request, user, SimpleNamespace(changed_data=["email"]), change=True)

    user.refresh_from_db()
    assert user.email_verified_at is None
    assert _unused(OtpChallenge.Purpose.EMAIL_VERIFY).count() == 0
    challenge = OtpChallenge.objects.get(purpose=OtpChallenge.Purpose.EMAIL_VERIFY)
    assert challenge.consumed_at is not None
    # The timestamp is today's, not a pre-set value.
    assert challenge.consumed_at <= timezone.now()
