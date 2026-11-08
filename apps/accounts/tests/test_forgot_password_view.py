"""Tests for the forgot-password page (D73)."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.auth_presentation import FORGOT_SENT_MESSAGE
from apps.accounts.models import User
from apps.notifications.tests.recording import RecordingSmsBackend

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
EMAIL = "asha@example.com"
PASSWORD = "OldPass123"


@pytest.fixture(autouse=True)
def _recording_sms(settings):
    settings.SMS_BACKEND = "apps.notifications.tests.recording.RecordingSmsBackend"
    RecordingSmsBackend.codes.clear()
    yield
    RecordingSmsBackend.codes.clear()


def _customer() -> User:
    return User.objects.create_user(phone=PHONE, password=PASSWORD, email=EMAIL)


def _forgot_path() -> str:
    return reverse("account_forgot_password")


def _post(client: Client, identifier: str):
    return client.post(_forgot_path(), {"identifier": identifier})


def test_forgot_page_shows_the_form() -> None:
    html = Client().get(_forgot_path()).content.decode()

    assert "data-forgot-form" in html
    assert 'name="identifier"' in html


def test_forgot_post_with_a_phone_sends_a_code_and_redirects_to_reset() -> None:
    _customer()
    client = Client()

    response = _post(client, PHONE)

    assert response.status_code == 302
    assert response["Location"] == reverse("account_reset_password")
    assert len(RecordingSmsBackend.codes) == 1
    assert client.session["account_reset_phone"] == PHONE
    assert client.session["account_reset_channel"] == "phone"


def test_forgot_post_with_an_email_sends_a_code_and_redirects_to_reset(mailoutbox) -> None:
    _customer()
    client = Client()

    response = _post(client, EMAIL)

    assert response.status_code == 302
    assert response["Location"] == reverse("account_reset_password")
    assert len(mailoutbox) == 1
    assert client.session["account_reset_channel"] == "email"


def test_forgot_post_for_an_unknown_account_gives_the_same_response() -> None:
    _customer()
    known = Client()
    unknown = Client()

    known_response = _post(known, PHONE)
    unknown_response = _post(unknown, "+919999999999")

    assert known_response.status_code == unknown_response.status_code == 302
    assert known_response["Location"] == unknown_response["Location"]
    assert len(RecordingSmsBackend.codes) == 1


def test_forgot_post_when_asked_too_soon_gives_the_same_response() -> None:
    _customer()
    client = Client()
    first = _post(client, PHONE)

    second = _post(Client(), PHONE)

    assert first.status_code == second.status_code == 302
    assert first["Location"] == second["Location"]
    assert len(RecordingSmsBackend.codes) == 1


def test_forgot_post_stores_only_the_phone_and_channel_in_the_session() -> None:
    _customer()
    client = Client()

    _post(client, PHONE)

    # The messages framework keeps its own "_messages" key; nothing else from
    # the posted identifier may stay in the session.
    stored = set(client.session.keys()) - {"_messages"}
    assert stored == {"account_reset_phone", "account_reset_channel"}
    assert client.session["account_reset_phone"] == PHONE


def test_forgot_post_with_a_blank_identifier_shows_an_error() -> None:
    response = _post(Client(), "")

    html = response.content.decode()
    assert response.status_code == 200
    assert "data-field-error" in html
    assert RecordingSmsBackend.codes == []


def test_forgot_page_needs_a_csrf_token() -> None:
    _customer()
    client = Client(enforce_csrf_checks=True)

    response = _post(client, PHONE)

    assert response.status_code == 403
    assert RecordingSmsBackend.codes == []


def test_forgot_page_rejects_put() -> None:
    response = Client().put(_forgot_path())

    assert response.status_code == 405


def test_forgot_page_is_not_cached() -> None:
    response = Client().get(_forgot_path())

    assert "no-store" in response["Cache-Control"]


def test_login_page_links_to_forgot_password() -> None:
    html = Client().get(reverse("account_login")).content.decode()

    assert "data-forgot-password-link" in html
    assert f'href="{_forgot_path()}"' in html


def test_forgot_response_never_names_the_phone_or_email() -> None:
    _customer()
    client = Client()

    response = _post(client, PHONE)
    followed = client.get(reverse("account_reset_password"))

    assert PHONE not in response.content.decode()
    assert PHONE not in followed.content.decode()
    assert EMAIL not in followed.content.decode()
    assert str(FORGOT_SENT_MESSAGE) in followed.content.decode()
