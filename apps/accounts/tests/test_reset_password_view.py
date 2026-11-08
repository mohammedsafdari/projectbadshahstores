"""Tests for the reset-password page (D73)."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.auth_presentation import (
    EMAIL_REMOVED_MESSAGE,
    PASSWORD_CHANGED_MESSAGE,
    RESET_CODE_ERROR_MESSAGE,
    RESET_NEED_REQUEST_MESSAGE,
)
from apps.accounts.models import OtpChallenge, User
from apps.accounts.passwords import (
    REASON_OK,
    REASON_WRONG,
    authenticate_customer_password,
    login_locked,
    record_login_failure,
)
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
    cache.clear()
    yield
    cache.clear()


def _customer(*, password: str | None = OLD_PASSWORD, email: str | None = EMAIL) -> User:
    return User.objects.create_user(phone=PHONE, password=password, email=email)


def _forgot_path() -> str:
    return reverse("account_forgot_password")


def _reset_path() -> str:
    return reverse("account_reset_password")


def _start(client: Client, identifier: str = PHONE):
    return client.post(_forgot_path(), {"identifier": identifier})


def _set_session(client: Client, phone: str = PHONE, channel: str = "phone") -> None:
    session = client.session
    session["account_reset_phone"] = phone
    session["account_reset_channel"] = channel
    session.save()


def _post(
    client: Client,
    code: str,
    *,
    password1: str = NEW_PASSWORD,
    password2: str = NEW_PASSWORD,
    follow: bool = False,
):
    return client.post(
        _reset_path(),
        {
            "code": code,
            "new_password1": password1,
            "new_password2": password2,
        },
        follow=follow,
    )


def _last_code() -> str:
    return RecordingSmsBackend.codes[-1][1]


def _email_code(mailoutbox) -> str:
    match = re.search(r"\b(\d{6})\b", mailoutbox[-1].body)
    assert match is not None
    return match.group(1)


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def test_reset_page_without_a_session_redirects_to_forgot() -> None:
    client = Client()

    response = client.get(_reset_path())
    followed = client.get(_reset_path(), follow=True)

    assert response.status_code == 302
    assert response["Location"] == _forgot_path()
    assert str(RESET_NEED_REQUEST_MESSAGE) in followed.content.decode()


def test_reset_page_shows_the_form_with_a_session() -> None:
    client = Client()
    _set_session(client)

    html = client.get(_reset_path()).content.decode()

    assert "data-reset-form" in html
    assert 'name="code"' in html
    assert 'name="new_password1"' in html


def test_right_code_and_good_password_sets_the_password_and_redirects_to_login() -> None:
    user = _customer()
    client = Client()
    _start(client)

    response = _post(client, _last_code())

    assert response.status_code == 302
    assert response["Location"] == reverse("account_login")
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD) is True


def test_reset_does_not_log_the_customer_in() -> None:
    _customer()
    client = Client()
    _start(client)

    _post(client, _last_code())

    assert "_auth_user_id" not in client.session


def test_reset_clears_the_session_keys() -> None:
    _customer()
    client = Client()
    _start(client)

    _post(client, _last_code())

    assert "account_reset_phone" not in client.session
    assert "account_reset_channel" not in client.session


def test_old_password_no_longer_works_and_the_new_one_does() -> None:
    _customer()
    client = Client()
    _start(client)
    _post(client, _last_code())

    old = authenticate_customer_password(PHONE, OLD_PASSWORD)
    new = authenticate_customer_password(PHONE, NEW_PASSWORD)

    assert old.reason == REASON_WRONG
    assert new.reason == REASON_OK


def test_wrong_code_shows_one_generic_error_and_changes_nothing() -> None:
    user = _customer()
    client = Client()
    _start(client)

    response = _post(client, _wrong(_last_code()))

    html = response.content.decode()
    assert response.status_code == 200
    assert str(RESET_CODE_ERROR_MESSAGE) in html
    assert html.count("data-field-error") == 0
    user.refresh_from_db()
    assert user.check_password(OLD_PASSWORD) is True


def test_expired_code_shows_the_same_error() -> None:
    user = _customer()
    client = Client()
    _start(client)
    OtpChallenge.objects.filter(phone=PHONE).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )

    response = _post(client, _last_code())

    assert str(RESET_CODE_ERROR_MESSAGE) in response.content.decode()
    user.refresh_from_db()
    assert user.check_password(OLD_PASSWORD) is True


def test_weak_password_shows_the_validator_message_and_keeps_the_code_usable() -> None:
    user = _customer()
    client = Client()
    _start(client)
    code = _last_code()

    weak = _post(client, code, password1="12345678", password2="12345678")
    good = _post(client, code)

    assert weak.status_code == 200
    assert "new_password1" in weak.context["form"].errors
    assert good.status_code == 302
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD) is True


def test_different_passwords_show_an_error() -> None:
    user = _customer()
    client = Client()
    _start(client)

    response = _post(client, _last_code(), password1="StrongPass1", password2="OtherPass2")

    assert response.status_code == 200
    assert "new_password2" in response.context["form"].errors
    user.refresh_from_db()
    assert user.check_password(OLD_PASSWORD) is True


def test_five_wrong_codes_burn_the_code() -> None:
    user = _customer()
    client = Client()
    _start(client)
    right = _last_code()
    wrong = _wrong(right)

    for _attempt in range(5):
        _post(client, wrong)
    response = _post(client, right)

    assert str(RESET_CODE_ERROR_MESSAGE) in response.content.decode()
    user.refresh_from_db()
    assert user.check_password(OLD_PASSWORD) is True


def test_unknown_account_session_shows_the_same_error() -> None:
    client = Client()
    _set_session(client, phone="+919999999999")

    response = _post(client, "123456")

    assert response.status_code == 200
    assert str(RESET_CODE_ERROR_MESSAGE) in response.content.decode()


def test_phone_reset_of_an_unverified_account_shows_the_email_removed_message() -> None:
    user = _customer()
    client = Client()
    _start(client)

    response = _post(client, _last_code(), follow=True)

    html = response.content.decode()
    assert str(PASSWORD_CHANGED_MESSAGE) in html
    assert str(EMAIL_REMOVED_MESSAGE) in html
    user.refresh_from_db()
    assert user.email is None


def test_reset_needs_a_csrf_token() -> None:
    _customer()
    client = Client(enforce_csrf_checks=True)
    _set_session(client)

    response = _post(client, "123456")

    assert response.status_code == 403


def test_reset_rejects_put() -> None:
    response = Client().put(_reset_path())

    assert response.status_code == 405


def test_reset_page_is_not_cached() -> None:
    client = Client()
    _set_session(client)

    response = client.get(_reset_path())

    assert "no-store" in response["Cache-Control"]


def test_reset_gives_an_otp_only_customer_a_first_password() -> None:
    user = User.objects.create_user(phone=PHONE)
    client = Client()
    _start(client)

    response = _post(client, _last_code())

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.has_usable_password() is True
    assert user.check_password(NEW_PASSWORD) is True


def test_reset_unlocks_a_locked_account() -> None:
    user = _customer()
    for _attempt in range(5):
        record_login_failure(user)
    assert login_locked(user) is True
    client = Client()
    _start(client)

    _post(client, _last_code())

    assert login_locked(user) is False
