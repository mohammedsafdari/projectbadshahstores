"""Tests for the forgot-password service (D73)."""

from __future__ import annotations

import re

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.accounts import password_reset
from apps.accounts.models import OtpChallenge, User
from apps.accounts.otp import request_code
from apps.accounts.password_reset import (
    CHANNEL_EMAIL,
    CHANNEL_PHONE,
    confirm_password_reset,
    request_password_reset,
)
from apps.accounts.passwords import login_locked, record_login_failure
from apps.notifications.tests.recording import RecordingSmsBackend

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
EMAIL = "asha@example.com"
OLD_PASSWORD = "OldPass123"
NEW_PASSWORD = "NewPass456"


@pytest.fixture(autouse=True)
def _recording_sms(settings):
    settings.SMS_BACKEND = "apps.notifications.tests.recording.RecordingSmsBackend"
    RecordingSmsBackend.codes.clear()
    yield
    RecordingSmsBackend.codes.clear()


@pytest.fixture(autouse=True)
def _clear_cache():
    # The login throttle lives in the cache, so no test inherits a lock.
    cache.clear()
    yield
    cache.clear()


def _customer(*, password: str | None = OLD_PASSWORD, email: str | None = EMAIL) -> User:
    return User.objects.create_user(phone=PHONE, password=password, email=email)


def _last_code() -> str:
    return RecordingSmsBackend.codes[-1][1]


def _email_code(mailoutbox) -> str:
    match = re.search(r"\b(\d{6})\b", mailoutbox[-1].body)
    assert match is not None
    return match.group(1)


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def _reset(code: str, *, channel: str = CHANNEL_PHONE, phone: str = PHONE):
    return confirm_password_reset(
        phone=phone,
        channel=channel,
        code=code,
        new_password=NEW_PASSWORD,
    )


def test_request_reset_by_phone_sends_an_sms_code() -> None:
    _customer()

    result = request_password_reset(PHONE)

    assert result.phone == PHONE
    assert result.channel == CHANNEL_PHONE
    assert len(RecordingSmsBackend.codes) == 1
    challenge = OtpChallenge.objects.get(phone=PHONE)
    assert challenge.purpose == OtpChallenge.Purpose.PASSWORD_RESET


def test_request_reset_by_email_sends_an_email_code(mailoutbox) -> None:
    _customer()

    result = request_password_reset(EMAIL)

    assert result.phone == PHONE
    assert result.channel == CHANNEL_EMAIL
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [EMAIL]
    challenge = OtpChallenge.objects.get(phone=PHONE)
    assert challenge.purpose == OtpChallenge.Purpose.PASSWORD_RESET


def test_request_reset_for_an_unknown_account_sends_nothing_and_looks_the_same() -> None:
    unknown_phone = request_password_reset("+919999999999")
    unknown_email = request_password_reset("nobody@example.com")

    assert unknown_phone == request_password_reset("+919999999999")
    assert unknown_phone.phone == "+919999999999"
    assert unknown_phone.channel == CHANNEL_PHONE
    assert unknown_email.phone == ""
    assert unknown_email.channel == CHANNEL_EMAIL
    assert RecordingSmsBackend.codes == []
    assert OtpChallenge.objects.count() == 0


def test_request_reset_for_a_staff_phone_sends_nothing() -> None:
    User.objects.create_user(phone=PHONE, password=OLD_PASSWORD, is_staff=True)

    result = request_password_reset(PHONE)

    assert result.phone == PHONE
    assert RecordingSmsBackend.codes == []
    assert OtpChallenge.objects.count() == 0


def test_request_reset_for_an_inactive_customer_sends_nothing() -> None:
    user = _customer()
    user.is_active = False
    user.save(update_fields=["is_active", "updated_at"])

    result = request_password_reset(PHONE)

    assert result.phone == PHONE
    assert RecordingSmsBackend.codes == []
    assert OtpChallenge.objects.count() == 0


def test_request_reset_is_rate_limited_like_other_codes() -> None:
    _customer()

    first = request_password_reset(PHONE)
    second = request_password_reset(PHONE)

    assert first.phone == second.phone == PHONE
    assert first.channel == second.channel == CHANNEL_PHONE
    assert len(RecordingSmsBackend.codes) == 1
    assert OtpChallenge.objects.count() == 1


def test_confirm_reset_sets_the_new_password() -> None:
    user = _customer()
    before = user.updated_at
    request_password_reset(PHONE)

    outcome = _reset(_last_code())

    assert outcome.ok is True
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD) is True
    assert user.password != OLD_PASSWORD
    # The save(update_fields=...) writes updated_at as well.
    assert user.updated_at > before


def test_confirm_reset_with_a_wrong_code_changes_nothing() -> None:
    user = _customer()
    request_password_reset(PHONE)

    outcome = _reset(_wrong(_last_code()))

    assert outcome.ok is False
    user.refresh_from_db()
    assert user.check_password(OLD_PASSWORD) is True


def test_confirm_reset_cannot_use_a_login_code() -> None:
    user = _customer()
    request_code(PHONE)

    outcome = _reset(_last_code())

    assert outcome.ok is False
    user.refresh_from_db()
    assert user.check_password(OLD_PASSWORD) is True


def test_phone_reset_marks_the_phone_verified() -> None:
    user = _customer(email=None)
    request_password_reset(PHONE)

    outcome = _reset(_last_code())

    assert outcome.ok is True
    user.refresh_from_db()
    assert user.phone_verified_at is not None


def test_email_reset_marks_the_email_verified_and_leaves_the_phone(mailoutbox) -> None:
    user = _customer()
    request_password_reset(EMAIL)

    outcome = _reset(_email_code(mailoutbox), channel=CHANNEL_EMAIL)

    assert outcome.ok is True
    user.refresh_from_db()
    assert user.email_verified_at is not None
    assert user.phone_verified_at is None


def test_phone_reset_of_an_unverified_account_removes_the_email_address() -> None:
    user = _customer()
    request_password_reset(PHONE)

    outcome = _reset(_last_code())

    assert outcome.ok is True
    assert outcome.email_removed is True
    user.refresh_from_db()
    assert user.email is None
    assert user.email_verified_at is None
    assert user.phone_verified_at is not None


def test_phone_reset_of_a_verified_account_keeps_the_email_address() -> None:
    user = _customer()
    user.phone_verified_at = timezone.now()
    user.email_verified_at = timezone.now()
    user.save(update_fields=["phone_verified_at", "email_verified_at", "updated_at"])
    request_password_reset(PHONE)

    outcome = _reset(_last_code())

    assert outcome.ok is True
    assert outcome.email_removed is False
    user.refresh_from_db()
    assert user.email == EMAIL
    assert user.email_verified_at is not None


def test_reset_clears_the_login_lock() -> None:
    user = _customer()
    for _attempt in range(5):
        record_login_failure(user)
    assert login_locked(user) is True
    request_password_reset(PHONE)

    outcome = _reset(_last_code())

    assert outcome.ok is True
    assert login_locked(user) is False


def test_reset_never_touches_a_staff_account_with_the_same_phone() -> None:
    staff = User.objects.create_user(phone=PHONE, password="StaffPass1", is_staff=True)
    user = _customer()
    request_password_reset(PHONE)

    outcome = _reset(_last_code())

    assert outcome.ok is True
    user.refresh_from_db()
    staff.refresh_from_db()
    assert user.check_password(NEW_PASSWORD) is True
    assert staff.check_password("StaffPass1") is True
    assert staff.phone_verified_at is None


def test_unknown_account_reset_still_runs_a_code_check(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    real_verify = password_reset.verify_code

    def _recording_verify(phone: str, code: str, **kwargs: object) -> object:
        calls.append((phone, code))
        return real_verify(phone, code, **kwargs)

    monkeypatch.setattr(password_reset, "verify_code", _recording_verify)
    unknown = "+919999999999"

    outcome = confirm_password_reset(
        phone=unknown,
        channel=CHANNEL_PHONE,
        code="123456",
        new_password=NEW_PASSWORD,
    )

    assert outcome.ok is False
    assert calls == [(unknown, "123456")]
