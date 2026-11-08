"""Tests for the phone and email verification service (D71).

The OTP module still owns the code and the rate limits, so these tests focus on
what the verification layer adds: the timestamp, the email delivery and the
OTP-login takeover.
"""

from __future__ import annotations

import re

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OtpChallenge, User
from apps.accounts.otp import request_code
from apps.accounts.verification import (
    REASON_NO_EMAIL,
    confirm_email_code,
    confirm_phone_code,
    request_email_code,
    request_phone_code,
)
from apps.accounts.verification_presentation import PASSWORD_RESET_FOR_SAFETY_MESSAGE
from apps.notifications.sms import SmsBackend
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"

# Every code the fake backend was asked to send, oldest first.
SENT: list[tuple[str, str]] = []


class RecordingSmsBackend(SmsBackend):
    """Keep the codes in memory instead of printing or sending them."""

    def send_otp(self, phone: str, code: str) -> None:
        SENT.append((phone, code))


@pytest.fixture(autouse=True)
def _recording_sms(settings):
    """Point SMS_BACKEND at the fake and empty the list around every test."""
    settings.SMS_BACKEND = f"{__name__}.RecordingSmsBackend"
    SENT.clear()
    yield
    SENT.clear()


def _last_sms_code() -> str:
    return SENT[-1][1]


def _email_code(mailoutbox) -> str:
    """Read the six-digit code out of the newest email."""
    match = re.search(r"\b(\d{6})\b", mailoutbox[-1].body)
    assert match is not None
    return match.group(1)


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def _start(client: Client, phone: str = PHONE):
    return client.post(reverse("account_login"), {"phone": phone})


def _verify(client: Client, code: str):
    return client.post(reverse("account_verify"), {"code": code})


# ---------------------------------------------------------------------------
# Asking for a code
# ---------------------------------------------------------------------------
def test_request_phone_code_sends_an_sms_to_the_account_phone() -> None:
    user = UserFactory(phone_verified_at=None)

    result = request_phone_code(user)

    assert result.sent is True
    assert SENT[0][0] == user.phone
    challenge = OtpChallenge.objects.get(phone=user.phone)
    assert challenge.purpose == OtpChallenge.Purpose.PHONE_VERIFY


def test_request_email_code_sends_an_email_to_the_account_email(mailoutbox) -> None:
    user = UserFactory(email="asha@example.com", email_verified_at=None)

    result = request_email_code(user)

    assert result.sent is True
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == ["asha@example.com"]
    assert len(_email_code(mailoutbox)) == 6
    challenge = OtpChallenge.objects.get(phone=user.phone)
    assert challenge.purpose == OtpChallenge.Purpose.EMAIL_VERIFY


def test_request_email_code_without_an_email_sends_nothing_and_says_why(mailoutbox) -> None:
    user = UserFactory(email=None, email_verified_at=None)

    result = request_email_code(user)

    assert result.sent is False
    assert result.reason == REASON_NO_EMAIL
    assert result.phone == user.phone
    assert mailoutbox == []
    assert OtpChallenge.objects.count() == 0


# ---------------------------------------------------------------------------
# Confirming a code
# ---------------------------------------------------------------------------
def test_confirm_phone_code_sets_phone_verified_at() -> None:
    user = UserFactory(phone_verified_at=None)
    request_phone_code(user)

    assert confirm_phone_code(user, _last_sms_code()) is True

    user.refresh_from_db()
    assert user.phone_verified_at is not None


def test_confirm_email_code_sets_email_verified_at(mailoutbox) -> None:
    user = UserFactory(email_verified_at=None)
    request_email_code(user)

    assert confirm_email_code(user, _email_code(mailoutbox)) is True

    user.refresh_from_db()
    assert user.email_verified_at is not None


def test_wrong_phone_code_changes_nothing() -> None:
    user = UserFactory(phone_verified_at=None)
    request_phone_code(user)

    assert confirm_phone_code(user, _wrong(_last_sms_code())) is False

    user.refresh_from_db()
    assert user.phone_verified_at is None


def test_wrong_email_code_changes_nothing(mailoutbox) -> None:
    user = UserFactory(email_verified_at=None)
    request_email_code(user)

    assert confirm_email_code(user, _wrong(_email_code(mailoutbox))) is False

    user.refresh_from_db()
    assert user.email_verified_at is None


def test_phone_code_cannot_confirm_the_email() -> None:
    user = UserFactory(email_verified_at=None)
    request_phone_code(user)

    assert confirm_email_code(user, _last_sms_code()) is False

    user.refresh_from_db()
    assert user.email_verified_at is None


def test_login_code_cannot_confirm_the_phone() -> None:
    user = UserFactory(phone_verified_at=None)
    request_code(user.phone)

    assert confirm_phone_code(user, _last_sms_code()) is False

    user.refresh_from_db()
    assert user.phone_verified_at is None


def test_confirm_on_an_already_verified_phone_keeps_the_first_time() -> None:
    user = UserFactory()
    first_time = user.phone_verified_at
    request_phone_code(user)

    assert confirm_phone_code(user, _last_sms_code()) is True

    user.refresh_from_db()
    assert user.phone_verified_at == first_time


def test_requests_are_rate_limited_like_login_codes() -> None:
    user = UserFactory(phone_verified_at=None)

    first = request_phone_code(user)
    second = request_phone_code(user)

    assert first.sent is True
    assert second.sent is False
    assert second.reason == "too_soon"
    assert len(SENT) == 1


def test_confirming_does_not_touch_the_password() -> None:
    user = UserFactory(phone_verified_at=None)
    user.set_password("Secret123")
    user.save(update_fields=["password"])
    stored_hash = user.password
    request_phone_code(user)

    assert confirm_phone_code(user, _last_sms_code()) is True

    user.refresh_from_db()
    assert user.password == stored_hash
    assert user.check_password("Secret123") is True


# ---------------------------------------------------------------------------
# The OTP login takeover
# ---------------------------------------------------------------------------
def test_otp_login_marks_a_new_account_verified() -> None:
    client = Client()
    _start(client)

    _verify(client, _last_sms_code())

    user = User.objects.get(phone=PHONE)
    assert user.phone_verified_at is not None
    assert user.has_usable_password() is False
    assert user.is_ready_for_delivery is True


def test_otp_login_of_an_unverified_password_account_clears_the_password_and_email() -> None:
    user = User.objects.create_user(
        phone=PHONE,
        password="Secret123",
        email="asha@example.com",
    )
    user.email_verified_at = timezone.now()
    user.save(update_fields=["email_verified_at"])
    assert user.has_usable_password() is True
    client = Client()
    _start(client)

    response = _verify(client, _last_sms_code())

    user.refresh_from_db()
    assert user.phone_verified_at is not None
    assert user.has_usable_password() is False
    assert user.email_verified_at is None
    stored = [str(message) for message in get_messages(response.wsgi_request)]
    assert str(PASSWORD_RESET_FOR_SAFETY_MESSAGE) in stored


def test_otp_login_of_a_verified_account_keeps_the_password() -> None:
    user = User.objects.create_user(
        phone=PHONE,
        password="Secret123",
        email="asha@example.com",
    )
    user.phone_verified_at = timezone.now()
    user.email_verified_at = timezone.now()
    user.save(update_fields=["phone_verified_at", "email_verified_at"])
    client = Client()
    _start(client)

    _verify(client, _last_sms_code())

    user.refresh_from_db()
    assert user.check_password("Secret123") is True
    assert user.email_verified_at is not None


def test_otp_login_still_refuses_staff_accounts() -> None:
    User.objects.create_user(phone=PHONE, password="Secret123", is_staff=True)
    client = Client()
    _start(client)

    response = _verify(client, _last_sms_code())

    assert response.status_code == 200
    assert "That code is not valid. Please try again." in response.content.decode()
    assert "_auth_user_id" not in client.session
    user = User.objects.get(phone=PHONE)
    assert user.phone_verified_at is None
