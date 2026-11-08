"""Tests for the phone and email verification pages (D71)."""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.verification_presentation import (
    CODE_ERROR_MESSAGE,
    CODE_SENT_MESSAGE,
    NO_EMAIL_MESSAGE,
    VERIFIED_MESSAGE,
    WAIT_MESSAGE,
    mask_email,
    mask_phone,
)
from apps.notifications.sms import SmsBackend
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
EMAIL = "asha@example.com"

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


def _phone_path() -> str:
    return reverse("account_verify_phone")


def _email_path() -> str:
    return reverse("account_verify_email")


def _email_code(mailoutbox) -> str:
    """Read the six-digit code out of the newest email."""
    match = re.search(r"\b(\d{6})\b", mailoutbox[-1].body)
    assert match is not None
    return match.group(1)


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def _send(client: Client, path: str):
    return client.post(path, {"action": "send"})


def _confirm(client: Client, path: str, code: str):
    return client.post(path, {"action": "confirm", "code": code})


def test_verify_phone_requires_login() -> None:
    response = Client().get(_phone_path())

    assert response.status_code == 302
    assert response.url.startswith(f"{reverse('account_login')}?next=")


def test_verify_email_requires_login() -> None:
    response = Client().get(_email_path())

    assert response.status_code == 302
    assert response.url.startswith(f"{reverse('account_login')}?next=")


def test_verify_phone_page_shows_the_masked_phone() -> None:
    user = UserFactory(phone=PHONE, phone_verified_at=None)
    client = Client()
    client.force_login(user)

    html = client.get(_phone_path()).content.decode()

    assert mask_phone(PHONE) in html


def test_verify_email_page_shows_the_masked_email() -> None:
    user = UserFactory(email=EMAIL, email_verified_at=None)
    client = Client()
    client.force_login(user)

    html = client.get(_email_path()).content.decode()

    assert mask_email(EMAIL) in html


def test_verify_email_page_without_an_email_explains_it_and_has_no_form() -> None:
    user = UserFactory(email=None, email_verified_at=None)
    client = Client()
    client.force_login(user)

    html = client.get(_email_path()).content.decode()

    assert str(NO_EMAIL_MESSAGE) in html
    assert 'name="action"' not in html


def test_send_phone_code_sends_the_sms_and_shows_a_message() -> None:
    user = UserFactory(phone_verified_at=None)
    client = Client()
    client.force_login(user)

    response = _send(client, _phone_path())
    followed = client.get(_phone_path())

    assert response.status_code == 302
    assert SENT[-1][0] == user.phone
    assert str(CODE_SENT_MESSAGE) in followed.content.decode()


def test_send_email_code_sends_the_email_and_shows_a_message(mailoutbox) -> None:
    user = UserFactory(email=EMAIL, email_verified_at=None)
    client = Client()
    client.force_login(user)

    response = _send(client, _email_path())
    followed = client.get(_email_path())

    assert response.status_code == 302
    assert len(mailoutbox) == 1
    assert str(CODE_SENT_MESSAGE) in followed.content.decode()


def test_right_phone_code_verifies_and_shows_the_done_state() -> None:
    user = UserFactory(phone_verified_at=None)
    client = Client()
    client.force_login(user)
    _send(client, _phone_path())

    response = _confirm(client, _phone_path(), SENT[-1][1])
    html = client.get(_phone_path()).content.decode()

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.phone_verified_at is not None
    assert str(VERIFIED_MESSAGE) in html
    assert 'name="action"' not in html


def test_right_email_code_verifies_and_shows_the_done_state(mailoutbox) -> None:
    user = UserFactory(email=EMAIL, email_verified_at=None)
    client = Client()
    client.force_login(user)
    _send(client, _email_path())

    response = _confirm(client, _email_path(), _email_code(mailoutbox))
    html = client.get(_email_path()).content.decode()

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.email_verified_at is not None
    assert str(VERIFIED_MESSAGE) in html
    assert 'name="action"' not in html


def test_wrong_code_shows_one_generic_message() -> None:
    user = UserFactory(phone_verified_at=None)
    client = Client()
    client.force_login(user)
    _send(client, _phone_path())

    response = _confirm(client, _phone_path(), _wrong(SENT[-1][1]))

    assert response.status_code == 200
    assert str(CODE_ERROR_MESSAGE) in response.content.decode()
    user.refresh_from_db()
    assert user.phone_verified_at is None


def test_second_request_too_soon_shows_the_wait_message() -> None:
    user = UserFactory(phone_verified_at=None)
    client = Client()
    client.force_login(user)
    _send(client, _phone_path())

    response = client.post(_phone_path(), {"action": "send"}, follow=True)

    assert str(WAIT_MESSAGE) in response.content.decode()
    assert len(SENT) == 1


def test_verified_phone_page_shows_the_done_state_and_no_form() -> None:
    user = UserFactory(phone=PHONE)
    client = Client()
    client.force_login(user)

    html = client.get(_phone_path()).content.decode()

    assert str(VERIFIED_MESSAGE) in html
    assert 'name="action"' not in html


def test_pages_are_not_cached() -> None:
    user = UserFactory(phone_verified_at=None)
    client = Client()
    client.force_login(user)

    response = client.get(_phone_path())

    assert "no-store" in response["Cache-Control"]


def test_pages_refuse_put() -> None:
    user = UserFactory(phone_verified_at=None)
    client = Client()
    client.force_login(user)

    response = client.put(_phone_path())

    assert response.status_code == 405


def test_pages_need_a_csrf_token() -> None:
    user = UserFactory(phone_verified_at=None)
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)

    response = client.post(_phone_path(), {"action": "send"})

    assert response.status_code == 403
    assert SENT == []


def test_blank_code_is_refused_by_the_form() -> None:
    user = UserFactory(phone_verified_at=None)
    client = Client()
    client.force_login(user)

    response = _confirm(client, _phone_path(), "")

    assert response.status_code == 200
    assert "This field is required." in response.content.decode()
    user.refresh_from_db()
    assert user.phone_verified_at is None


def test_code_with_letters_is_refused_by_the_form() -> None:
    user = UserFactory(phone_verified_at=None)
    client = Client()
    client.force_login(user)

    response = _confirm(client, _phone_path(), "abcdef")

    assert response.status_code == 200
    assert "Enter the 6-digit code." in response.content.decode()
    user.refresh_from_db()
    assert user.phone_verified_at is None


def test_unknown_action_does_nothing() -> None:
    user = UserFactory(phone_verified_at=None)
    client = Client()
    client.force_login(user)

    response = client.post(_phone_path(), {"action": "bogus"})

    assert response.status_code == 200
    assert SENT == []
    user.refresh_from_db()
    assert user.phone_verified_at is None


def test_pages_never_show_the_full_phone_or_email() -> None:
    user = UserFactory(phone=PHONE, email=EMAIL, phone_verified_at=None, email_verified_at=None)
    client = Client()
    client.force_login(user)

    phone_html = client.get(_phone_path()).content.decode()
    email_html = client.get(_email_path()).content.decode()

    assert PHONE not in phone_html
    assert EMAIL not in email_html
