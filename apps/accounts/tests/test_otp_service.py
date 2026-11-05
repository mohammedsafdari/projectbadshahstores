"""Tests for the phone OTP service (Slice 30, D59).

The service owns the code, the HMAC, the expiry and the rate limits, so these
tests talk to it directly. The last-but-one test starts threads: the challenge
row is locked while attempts are counted, and every wrong try must be counted.
"""

from __future__ import annotations

import hmac
import logging
import threading
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from apps.accounts.models import OtpChallenge
from apps.accounts.otp import (
    MAX_ATTEMPTS,
    delete_old_challenges,
    request_code,
    verify_code,
)
from apps.notifications.sms import SmsBackend

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
OTHER_PHONE = "+919876543211"

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


def _challenge(phone: str = PHONE) -> OtpChallenge:
    return OtpChallenge.objects.get(phone=phone)


def _last_code() -> str:
    return SENT[-1][1]


def _wrong_code() -> str:
    """A six-digit code that is definitely not the one just sent."""
    right = _last_code()
    return f"{(int(right) + 1) % 1_000_000:06d}"


def _make_challenge(phone: str, created_at) -> OtpChallenge:
    return OtpChallenge.objects.create(
        phone=phone,
        purpose=OtpChallenge.Purpose.LOGIN,
        code_hmac="x" * 64,
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=5),
    )


# ---------------------------------------------------------------------------
# Asking for a code
# ---------------------------------------------------------------------------
def test_request_creates_a_challenge_and_sends_the_code_once() -> None:
    result = request_code(PHONE)

    assert result.sent is True
    assert OtpChallenge.objects.count() == 1
    assert len(SENT) == 1
    assert SENT[0][0] == PHONE


def test_the_stored_value_is_an_hmac_and_not_the_code() -> None:
    request_code(PHONE)
    challenge = _challenge()
    code = _last_code()

    assert challenge.code_hmac != code
    assert len(challenge.code_hmac) == 64
    # A hex digest is the only thing stored; the six digits are not.
    assert all(character in "0123456789abcdef" for character in challenge.code_hmac)


def test_the_code_is_six_digits() -> None:
    request_code(PHONE)
    code = _last_code()

    assert len(code) == 6
    assert code.isdigit()


def test_the_phone_is_normalised_before_use() -> None:
    request_code("98765 43210")

    assert _challenge().phone == PHONE
    assert SENT[0][0] == PHONE


# ---------------------------------------------------------------------------
# Checking a code
# ---------------------------------------------------------------------------
def test_right_code_returns_the_phone_and_consumes_the_challenge() -> None:
    request_code(PHONE)

    result = verify_code(PHONE, _last_code())

    assert result.ok is True
    assert result.phone == PHONE
    assert _challenge().consumed_at is not None


def test_wrong_code_fails_and_counts_an_attempt() -> None:
    request_code(PHONE)

    result = verify_code(PHONE, _wrong_code())

    assert result.ok is False
    assert result.phone is None
    assert result.reason == "wrong_code"
    assert _challenge().attempts == 1


def test_five_wrong_codes_kill_the_challenge_even_for_the_right_code() -> None:
    request_code(PHONE)
    right = _last_code()
    wrong = _wrong_code()

    for _attempt in range(MAX_ATTEMPTS):
        assert verify_code(PHONE, wrong).ok is False

    result = verify_code(PHONE, right)

    assert result.ok is False
    challenge = _challenge()
    assert challenge.attempts == MAX_ATTEMPTS
    assert challenge.consumed_at is not None


def test_an_expired_code_is_refused() -> None:
    request_code(PHONE)
    OtpChallenge.objects.filter(phone=PHONE).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )

    result = verify_code(PHONE, _last_code())

    assert result.ok is False
    assert result.reason == "expired"


def test_a_used_code_cannot_be_used_twice() -> None:
    request_code(PHONE)
    code = _last_code()
    assert verify_code(PHONE, code).ok is True

    second = verify_code(PHONE, code)

    assert second.ok is False
    assert second.reason == "no_challenge"


def test_a_new_request_invalidates_the_previous_code() -> None:
    request_code(PHONE)
    first_code = _last_code()
    # Age the first request, so the 30-second resend rule allows another one.
    OtpChallenge.objects.filter(phone=PHONE).update(
        created_at=timezone.now() - timedelta(seconds=61)
    )

    request_code(PHONE)
    second_code = _last_code()
    assert second_code != first_code

    assert verify_code(PHONE, first_code).ok is False
    assert verify_code(PHONE, second_code).ok is True


def test_a_code_for_another_phone_is_refused() -> None:
    request_code(PHONE)

    result = verify_code(OTHER_PHONE, _last_code())

    assert result.ok is False
    assert result.reason == "no_challenge"


def test_verify_compares_with_compare_digest(monkeypatch) -> None:
    request_code(PHONE)
    code = _last_code()
    calls: list[tuple[str, str]] = []
    original = hmac.compare_digest

    def spy(first: str, second: str) -> bool:
        calls.append((first, second))
        return original(first, second)

    # The service calls hmac.compare_digest, so patching it here proves the
    # constant-time comparison is really the one used.
    monkeypatch.setattr("apps.accounts.otp.hmac.compare_digest", spy)

    result = verify_code(PHONE, code)

    assert result.ok is True
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------------
def test_resend_within_30_seconds_is_refused() -> None:
    request_code(PHONE)

    result = request_code(PHONE)

    assert result.sent is False
    assert result.reason == "too_soon"
    assert OtpChallenge.objects.count() == 1
    assert len(SENT) == 1


def test_sixth_request_in_an_hour_is_refused() -> None:
    now = timezone.now()
    # Five requests spread over the last hour, the newest well past 30 seconds.
    for minutes in (50, 40, 30, 20, 10):
        _make_challenge(PHONE, now - timedelta(minutes=minutes))

    result = request_code(PHONE)

    assert result.sent is False
    assert result.reason == "too_many_requests"
    assert OtpChallenge.objects.count() == 5
    assert SENT == []


def test_limits_are_per_phone() -> None:
    request_code(PHONE)

    result = request_code(OTHER_PHONE)

    assert result.sent is True
    assert OtpChallenge.objects.filter(phone=PHONE).count() == 1
    assert OtpChallenge.objects.filter(phone=OTHER_PHONE).count() == 1
    assert [phone for phone, _code in SENT] == [PHONE, OTHER_PHONE]


# ---------------------------------------------------------------------------
# The lock: concurrent wrong codes must all count
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_concurrent_wrong_codes_are_all_counted() -> None:
    request_code(PHONE)
    wrong = _wrong_code()
    challenge_pk = _challenge().pk

    # A Barrier releases all four threads at the same moment, so their reads
    # overlap unless select_for_update() makes them queue.
    barrier = threading.Barrier(4)
    errors: list[BaseException] = []

    def try_wrong_code() -> None:
        try:
            barrier.wait(timeout=10)
            verify_code(PHONE, wrong)
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()  # this thread's own connection

    threads = [threading.Thread(target=try_wrong_code) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    challenge = OtpChallenge.objects.get(pk=challenge_pk)
    # Without the lock, each thread would read 0 and write 1: the final count
    # would be 1 instead of 4.
    assert challenge.attempts == 4
    assert challenge.consumed_at is None


# ---------------------------------------------------------------------------
# Logs and clean-up
# ---------------------------------------------------------------------------
def test_no_code_appears_in_the_logs(caplog) -> None:
    caplog.set_level(logging.DEBUG)

    request_code(PHONE)
    code = _last_code()

    assert code not in caplog.text


def test_old_challenges_are_deleted() -> None:
    now = timezone.now()
    old = _make_challenge(PHONE, now - timedelta(days=2))
    recent = _make_challenge(OTHER_PHONE, now)

    deleted = delete_old_challenges(days=1)

    assert deleted == 1
    assert not OtpChallenge.objects.filter(pk=old.pk).exists()
    assert OtpChallenge.objects.filter(pk=recent.pk).exists()
