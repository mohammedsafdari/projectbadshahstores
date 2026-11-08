"""Tests for the per-purpose OTP codes (D71).

The OTP service keeps the same rules as the LOGIN flow; these tests prove that
a purpose splits the codes and the rate limits without changing that flow.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import OtpChallenge
from apps.accounts.otp import _code_hmac, request_code, verify_code
from apps.notifications.sms import SmsBackend

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


def _last_code() -> str:
    return SENT[-1][1]


def _wrong_code() -> str:
    """A six-digit code that is definitely not the one just sent."""
    right = _last_code()
    return f"{(int(right) + 1) % 1_000_000:06d}"


def test_request_code_defaults_to_the_login_purpose() -> None:
    request_code(PHONE)

    challenge = OtpChallenge.objects.get(phone=PHONE)
    assert challenge.purpose == OtpChallenge.Purpose.LOGIN


def test_code_for_one_purpose_does_not_verify_another_purpose() -> None:
    request_code(PHONE, purpose=OtpChallenge.Purpose.PHONE_VERIFY)
    code = _last_code()

    login_result = verify_code(PHONE, code)

    assert login_result.ok is False
    assert login_result.reason == "no_challenge"
    assert verify_code(PHONE, code, purpose=OtpChallenge.Purpose.PHONE_VERIFY).ok is True


def test_rate_limit_is_counted_per_purpose() -> None:
    login = request_code(PHONE)
    phone = request_code(PHONE, purpose=OtpChallenge.Purpose.PHONE_VERIFY)

    # The 30-second rule is per purpose, so the second purpose is not refused.
    assert login.sent is True
    assert phone.sent is True
    assert OtpChallenge.objects.filter(phone=PHONE).count() == 2


def test_new_request_replaces_the_old_code_for_the_same_purpose_only() -> None:
    request_code(PHONE)
    first_code = _last_code()
    request_code(PHONE, purpose=OtpChallenge.Purpose.PHONE_VERIFY)
    phone_code = _last_code()
    # Age both requests, so the 30-second resend rule allows a new login code.
    OtpChallenge.objects.filter(phone=PHONE).update(
        created_at=timezone.now() - timedelta(seconds=61)
    )

    request_code(PHONE)

    assert verify_code(PHONE, first_code).ok is False
    # The phone-verification challenge was not touched by the login request.
    assert verify_code(PHONE, phone_code, purpose=OtpChallenge.Purpose.PHONE_VERIFY).ok is True


def test_wrong_code_attempts_are_counted_per_purpose() -> None:
    request_code(PHONE, purpose=OtpChallenge.Purpose.PHONE_VERIFY)
    wrong = _wrong_code()

    assert verify_code(PHONE, wrong, purpose=OtpChallenge.Purpose.PHONE_VERIFY).ok is False
    challenge = OtpChallenge.objects.get(phone=PHONE, purpose=OtpChallenge.Purpose.PHONE_VERIFY)
    assert challenge.attempts == 1

    # A wrong LOGIN try has no login challenge to count against.
    assert verify_code(PHONE, wrong).reason == "no_challenge"
    challenge.refresh_from_db()
    assert challenge.attempts == 1


def test_expired_code_is_refused_for_any_purpose() -> None:
    now = timezone.now()
    # Two expired challenges, each with the HMAC of its own code.
    for purpose, code in (
        (OtpChallenge.Purpose.PHONE_VERIFY, "111111"),
        (OtpChallenge.Purpose.EMAIL_VERIFY, "222222"),
    ):
        OtpChallenge.objects.create(
            phone=PHONE,
            purpose=purpose,
            code_hmac=_code_hmac(PHONE, purpose, code),
            created_at=now - timedelta(minutes=10),
            expires_at=now - timedelta(seconds=1),
        )

    phone = verify_code(PHONE, "111111", purpose=OtpChallenge.Purpose.PHONE_VERIFY)
    email = verify_code(PHONE, "222222", purpose=OtpChallenge.Purpose.EMAIL_VERIFY)

    assert phone.reason == "expired"
    assert email.reason == "expired"


def test_custom_sender_receives_the_code_and_the_sms_backend_does_not() -> None:
    received: list[str] = []

    result = request_code(
        PHONE,
        purpose=OtpChallenge.Purpose.EMAIL_VERIFY,
        send=received.append,
    )

    assert result.sent is True
    assert len(received) == 1
    assert len(received[0]) == 6
    assert received[0].isdigit()
    # The custom sender replaces the SMS call completely.
    assert SENT == []
    assert verify_code(PHONE, received[0], purpose=OtpChallenge.Purpose.EMAIL_VERIFY).ok is True


def test_challenge_stores_only_the_hmac_for_every_purpose() -> None:
    received: list[str] = []

    for purpose in (
        OtpChallenge.Purpose.PHONE_VERIFY,
        OtpChallenge.Purpose.EMAIL_VERIFY,
        OtpChallenge.Purpose.PASSWORD_RESET,
    ):
        request_code(PHONE, purpose=purpose, send=received.append)
        challenge = OtpChallenge.objects.get(phone=PHONE, purpose=purpose)
        code = received[-1]

        assert challenge.code_hmac != code
        assert len(challenge.code_hmac) == 64
        assert all(character in "0123456789abcdef" for character in challenge.code_hmac)


def test_verify_code_closes_other_unused_challenges_of_the_same_purpose_only() -> None:
    now = timezone.now()
    older = OtpChallenge.objects.create(
        phone=PHONE,
        purpose=OtpChallenge.Purpose.PHONE_VERIFY,
        code_hmac=_code_hmac(PHONE, OtpChallenge.Purpose.PHONE_VERIFY, "111111"),
        created_at=now - timedelta(minutes=2),
        expires_at=now + timedelta(minutes=3),
    )
    newer = OtpChallenge.objects.create(
        phone=PHONE,
        purpose=OtpChallenge.Purpose.PHONE_VERIFY,
        code_hmac=_code_hmac(PHONE, OtpChallenge.Purpose.PHONE_VERIFY, "222222"),
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    login = OtpChallenge.objects.create(
        phone=PHONE,
        purpose=OtpChallenge.Purpose.LOGIN,
        code_hmac=_code_hmac(PHONE, OtpChallenge.Purpose.LOGIN, "333333"),
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )

    result = verify_code(PHONE, "222222", purpose=OtpChallenge.Purpose.PHONE_VERIFY)

    assert result.ok is True
    older.refresh_from_db()
    newer.refresh_from_db()
    login.refresh_from_db()
    assert older.consumed_at is not None
    assert newer.consumed_at is not None
    # A code for another purpose is never closed by this verification.
    assert login.consumed_at is None


def test_hourly_limit_is_counted_per_purpose() -> None:
    now = timezone.now()
    # Five login challenges inside the rolling hour, spaced past the resend
    # rule so the hourly count is the only limit left.
    for index in range(5):
        OtpChallenge.objects.create(
            phone=PHONE,
            purpose=OtpChallenge.Purpose.LOGIN,
            code_hmac=_code_hmac(PHONE, OtpChallenge.Purpose.LOGIN, f"{index:06d}"),
            created_at=now - timedelta(minutes=index + 1),
            expires_at=now + timedelta(minutes=5),
        )

    login = request_code(PHONE)
    phone = request_code(PHONE, purpose=OtpChallenge.Purpose.PHONE_VERIFY)

    assert login.sent is False
    assert login.reason == "too_many_requests"
    # The phone-verification purpose has its own count, so it still sends.
    assert phone.sent is True
