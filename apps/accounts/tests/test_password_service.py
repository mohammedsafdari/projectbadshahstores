"""Password sign-up, customer lookup and the login throttle (Slice 38f, D72)."""

from __future__ import annotations

import time

import pytest
from django.core.cache import cache

from apps.accounts import passwords
from apps.accounts.models import User
from apps.accounts.passwords import (
    REASON_LOCKED,
    REASON_OK,
    REASON_WRONG,
    AccountExistsError,
    authenticate_customer_password,
    register_customer,
)

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
OTHER_PHONE = "+919876543211"
EMAIL = "asha@example.com"
OTHER_EMAIL = "ravi@example.com"
PASSWORD = "StrongPass1"


@pytest.fixture(autouse=True)
def _clear_cache():
    """The throttle lives in the cache, so every test starts with no counter."""
    cache.clear()
    yield
    cache.clear()


def _register(
    phone: str = PHONE,
    email: str = EMAIL,
    full_name: str = "Asha Rao",
    password: str = PASSWORD,
) -> User:
    return register_customer(
        phone=phone,
        email=email,
        full_name=full_name,
        password=password,
    )


def _fail_five(identifier: str = PHONE) -> None:
    for _attempt in range(5):
        result = authenticate_customer_password(identifier, "WrongPass9")
        assert result.reason == REASON_WRONG


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def test_register_creates_a_customer_with_a_usable_password() -> None:
    user = _register()

    assert user.is_staff is False
    assert user.is_superuser is False
    assert user.role == User.ROLE_MEMBER
    assert user.has_usable_password() is True
    assert user.check_password(PASSWORD) is True


def test_register_leaves_the_phone_and_email_unverified() -> None:
    user = _register()

    assert user.phone_verified_at is None
    assert user.email_verified_at is None


def test_register_stores_a_lowercase_email_and_a_normalised_phone() -> None:
    user = _register(phone="98765 43210", email="  ASHA@Example.COM ")

    assert user.phone == PHONE
    assert user.email == EMAIL


def test_register_refuses_an_existing_customer_phone() -> None:
    _register()

    with pytest.raises(AccountExistsError):
        _register(email=OTHER_EMAIL)


def test_register_refuses_a_deactivated_customers_phone() -> None:
    user = _register()
    user.is_active = False
    user.save(update_fields=["is_active"])

    with pytest.raises(AccountExistsError):
        _register(email=OTHER_EMAIL)


def test_register_refuses_an_existing_customer_email() -> None:
    _register()

    with pytest.raises(AccountExistsError):
        _register(phone=OTHER_PHONE)


def test_register_allows_a_phone_used_only_by_a_staff_account() -> None:
    staff = User.objects.create_user(phone=PHONE, password="StaffPass1", is_staff=True)

    customer = _register()

    assert customer.pk != staff.pk
    assert customer.is_staff is False


def test_register_turns_a_race_on_the_unique_constraint_into_account_exists(
    monkeypatch,
) -> None:
    User.objects.create_user(phone=PHONE, password=PASSWORD, email=EMAIL)
    # Let the pre-check pass, so the database constraint is the only guard.
    monkeypatch.setattr(passwords, "_raise_if_account_exists", lambda phone, email: None)

    with pytest.raises(AccountExistsError):
        _register()


# ---------------------------------------------------------------------------
# Password login
# ---------------------------------------------------------------------------
def test_login_with_phone_and_password() -> None:
    user = _register()

    result = authenticate_customer_password(PHONE, PASSWORD)

    assert result.reason == REASON_OK
    assert result.user is not None
    assert result.user.pk == user.pk


def test_login_with_email_and_password() -> None:
    _register()

    result = authenticate_customer_password(EMAIL, PASSWORD)

    assert result.reason == REASON_OK


def test_login_with_a_spaced_phone() -> None:
    _register()

    result = authenticate_customer_password("98765 43210", PASSWORD)

    assert result.reason == REASON_OK


def test_login_with_an_uppercase_email() -> None:
    _register()

    result = authenticate_customer_password("ASHA@Example.COM", PASSWORD)

    assert result.reason == REASON_OK


def test_wrong_password_fails() -> None:
    _register()

    result = authenticate_customer_password(PHONE, "WrongPass9")

    assert result.reason == REASON_WRONG
    assert result.user is None


def test_unknown_identifier_fails_the_same_way() -> None:
    result = authenticate_customer_password("+919999999999", PASSWORD)

    assert result.reason == REASON_WRONG
    assert result.user is None


def test_account_without_a_usable_password_fails() -> None:
    User.objects.create_user(phone=PHONE, email=EMAIL)

    result = authenticate_customer_password(PHONE, PASSWORD)

    assert result.reason == REASON_WRONG


def test_staff_account_cannot_log_in_with_its_password_here() -> None:
    User.objects.create_user(phone=PHONE, password="StaffPass1", is_staff=True)

    result = authenticate_customer_password(PHONE, "StaffPass1")

    assert result.reason == REASON_WRONG


def test_inactive_customer_cannot_log_in() -> None:
    user = _register()
    user.is_active = False
    user.save(update_fields=["is_active"])

    result = authenticate_customer_password(PHONE, PASSWORD)

    assert result.reason == REASON_WRONG


# ---------------------------------------------------------------------------
# The throttle
# ---------------------------------------------------------------------------
def test_five_wrong_passwords_lock_the_account() -> None:
    _register()

    for _attempt in range(5):
        assert authenticate_customer_password(PHONE, "WrongPass9").reason == REASON_WRONG

    assert authenticate_customer_password(PHONE, PASSWORD).reason == REASON_LOCKED


def test_locked_account_refuses_even_the_right_password() -> None:
    _register()
    _fail_five()

    result = authenticate_customer_password(PHONE, PASSWORD)

    assert result.reason == REASON_LOCKED
    assert result.user is None


def test_lock_ends_when_the_lock_time_has_passed(monkeypatch) -> None:
    _register()
    monkeypatch.setattr(passwords, "LOGIN_LOCK_SECONDS", 1)
    _fail_five()
    assert authenticate_customer_password(PHONE, PASSWORD).reason == REASON_LOCKED

    time.sleep(1.2)

    assert authenticate_customer_password(PHONE, PASSWORD).reason == REASON_OK


def test_right_password_clears_the_failure_count() -> None:
    _register()
    for _attempt in range(4):
        authenticate_customer_password(PHONE, "WrongPass9")

    assert authenticate_customer_password(PHONE, PASSWORD).reason == REASON_OK

    for _attempt in range(4):
        assert authenticate_customer_password(PHONE, "WrongPass9").reason == REASON_WRONG


def test_unknown_identifier_locks_like_a_real_one() -> None:
    unknown = "+919999999999"
    _fail_five(unknown)

    assert authenticate_customer_password(unknown, PASSWORD).reason == REASON_LOCKED


def test_failures_are_counted_per_account() -> None:
    _register()
    _register(phone=OTHER_PHONE, email=OTHER_EMAIL)
    for _attempt in range(4):
        authenticate_customer_password(PHONE, "WrongPass9")
        authenticate_customer_password(OTHER_PHONE, "WrongPass9")
    # The fifth failure for account A locks only A.
    authenticate_customer_password(PHONE, "WrongPass9")

    assert authenticate_customer_password(OTHER_PHONE, "WrongPass9").reason == REASON_WRONG
    assert authenticate_customer_password(OTHER_PHONE, PASSWORD).reason == REASON_LOCKED
    assert authenticate_customer_password(PHONE, PASSWORD).reason == REASON_LOCKED


def test_phone_and_email_of_one_account_share_the_failure_count() -> None:
    _register()
    for _attempt in range(3):
        authenticate_customer_password(PHONE, "WrongPass9")
    for _attempt in range(2):
        authenticate_customer_password(EMAIL, "WrongPass9")

    assert authenticate_customer_password(PHONE, PASSWORD).reason == REASON_LOCKED


def test_unknown_identifier_still_hashes_a_password(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    real_check = passwords.check_password

    # A small wrapper around the real check: it records each call, so the test
    # can prove that a password was hashed even when no account was found.
    def _recording_check(password: str, encoded: str) -> bool:
        calls.append((password, encoded))
        return real_check(password, encoded)

    monkeypatch.setattr(passwords, "check_password", _recording_check)

    authenticate_customer_password("+919999999999", PASSWORD)

    assert len(calls) == 1


def test_deactivated_customer_cannot_log_in_by_email() -> None:
    user = _register()
    user.is_active = False
    user.save(update_fields=["is_active"])

    result = authenticate_customer_password(EMAIL, PASSWORD)

    assert result.reason == REASON_WRONG
    assert result.user is None


def test_account_without_a_usable_password_still_hashes_a_password(monkeypatch) -> None:
    # An OTP-only account owns no hash of its own, so the login must still pay
    # for one hash check: its answer should cost like a normal wrong password.
    User.objects.create_user(phone=PHONE, email=EMAIL)
    normal = _register(phone=OTHER_PHONE, email=OTHER_EMAIL)

    calls: list[tuple[str, str]] = []
    real_check = passwords.check_password

    def _recording_check(password: str, encoded: str) -> bool:
        calls.append((password, encoded))
        return real_check(password, encoded)

    monkeypatch.setattr(passwords, "check_password", _recording_check)

    authenticate_customer_password(PHONE, PASSWORD)
    otp_only_calls = len(calls)

    calls.clear()
    # The normal login path calls ``user.check_password`` (a different binding),
    # so compare with one direct call against a normal account's real hash.
    assert passwords.check_password("WrongPass9", normal.password) is False

    assert len(calls) == otp_only_calls == 1


def test_account_without_a_usable_password_counts_toward_the_lock() -> None:
    User.objects.create_user(phone=PHONE, email=EMAIL)

    for _attempt in range(5):
        assert authenticate_customer_password(PHONE, PASSWORD).reason == REASON_WRONG

    assert authenticate_customer_password(PHONE, PASSWORD).reason == REASON_LOCKED
