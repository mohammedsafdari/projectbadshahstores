"""Tests for the phone login views (Slice 30, D59).

Every test uses the real URLs and the real session. A fake SMS backend records
the code, so the tests can post it back the way a customer would.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.notifications.sms import SmsBackend

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
OTHER_PHONE = "+919876543211"

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


def _start(client: Client, phone: str = PHONE, next_url: str | None = None):
    """Post the phone the way the login form does."""
    data = {"phone": phone}
    if next_url is not None:
        data["next"] = next_url
    return client.post(reverse("account_login"), data)


def _code() -> str:
    return SENT[-1][1]


def _wrong_code() -> str:
    return f"{(int(_code()) + 1) % 1_000_000:06d}"


def _verify(client: Client, code: str):
    return client.post(reverse("account_verify"), {"code": code})


# ---------------------------------------------------------------------------
# The login page
# ---------------------------------------------------------------------------
def test_login_page_renders() -> None:
    response = Client().get(reverse("account_login"))

    assert response.status_code == 200
    assert 'name="phone"' in response.content.decode()


def test_a_valid_phone_sends_a_code_and_redirects_to_verify() -> None:
    client = Client()

    response = _start(client)

    assert response.status_code == 302
    assert response["Location"] == reverse("account_verify")
    assert len(SENT) == 1
    assert SENT[0][0] == PHONE
    assert client.session["otp_phone"] == PHONE


def test_a_bad_phone_shows_an_error_and_sends_nothing() -> None:
    client = Client()

    response = client.post(reverse("account_login"), {"phone": "12345"})

    assert response.status_code == 200
    assert "Enter a valid Indian mobile number." in response.content.decode()
    assert SENT == []


def test_known_and_unknown_phones_get_the_same_response() -> None:
    User.objects.create_user(phone=PHONE)

    known = _start(Client(), PHONE)
    unknown = _start(Client(), OTHER_PHONE)

    assert known.status_code == 302
    assert unknown.status_code == 302
    assert known["Location"] == unknown["Location"] == reverse("account_verify")
    assert len(SENT) == 2


# ---------------------------------------------------------------------------
# Logging in
# ---------------------------------------------------------------------------
def test_right_code_creates_a_member_and_logs_in() -> None:
    client = Client()
    _start(client)

    response = _verify(client, _code())

    assert response.status_code == 302
    assert response["Location"] == reverse("home")
    user = User.objects.get(phone=PHONE)
    assert user.role == User.ROLE_MEMBER
    assert user.is_staff is False
    assert user.is_superuser is False
    assert user.has_usable_password() is False
    assert client.session["_auth_user_id"] == str(user.pk)


def test_right_code_logs_in_an_existing_customer_without_creating_another() -> None:
    user = User.objects.create_user(phone=PHONE)
    client = Client()
    _start(client)

    response = _verify(client, _code())

    assert response.status_code == 302
    assert User.objects.filter(phone=PHONE).count() == 1
    assert client.session["_auth_user_id"] == str(user.pk)


def test_wrong_code_shows_a_generic_error_and_does_not_log_in() -> None:
    client = Client()
    _start(client)

    response = _verify(client, _wrong_code())

    assert response.status_code == 200
    assert "That code is not valid. Please try again." in response.content.decode()
    assert "_auth_user_id" not in client.session
    assert not User.objects.exists()


def test_verify_without_a_session_phone_redirects_to_login() -> None:
    response = Client().get(reverse("account_verify"))

    assert response.status_code == 302
    assert response["Location"] == reverse("account_login")


def test_a_staff_account_cannot_log_in_with_an_otp() -> None:
    User.objects.create_user(phone=PHONE, password="ok", is_staff=True)
    client = Client()
    _start(client)

    response = _verify(client, _code())

    assert response.status_code == 200
    assert "That code is not valid. Please try again." in response.content.decode()
    assert "_auth_user_id" not in client.session
    assert User.objects.filter(phone=PHONE).count() == 1


def test_an_inactive_account_cannot_log_in_with_an_otp() -> None:
    inactive = User.objects.create_user(phone=PHONE)
    inactive.is_active = False
    inactive.save(update_fields=["is_active"])
    client = Client()
    _start(client)

    response = _verify(client, _code())

    assert response.status_code == 200
    assert "_auth_user_id" not in client.session
    assert User.objects.filter(phone=PHONE).count() == 1


# ---------------------------------------------------------------------------
# next, the session and logout
# ---------------------------------------------------------------------------
def test_next_on_the_same_host_is_followed() -> None:
    client = Client()
    _start(client, next_url="/products/")

    response = _verify(client, _code())

    assert response.status_code == 302
    assert response["Location"] == "/products/"


def test_next_on_another_host_is_ignored() -> None:
    client = Client()
    _start(client, next_url="https://evil.example/phish")

    response = _verify(client, _code())

    assert response.status_code == 302
    assert response["Location"] == reverse("home")


def test_login_changes_the_session_key() -> None:
    client = Client()
    _start(client)
    key_before = client.session.session_key
    assert key_before is not None

    _verify(client, _code())

    assert client.session.session_key is not None
    assert client.session.session_key != key_before


def test_logout_needs_post() -> None:
    user = User.objects.create_user(phone=PHONE)
    client = Client()
    client.force_login(user)

    response = client.get(reverse("account_logout"))

    assert response.status_code == 405


def test_logout_logs_the_user_out() -> None:
    user = User.objects.create_user(phone=PHONE)
    client = Client()
    client.force_login(user)

    response = client.post(reverse("account_logout"))

    assert response.status_code == 302
    assert response["Location"] == reverse("home")
    assert "_auth_user_id" not in client.session


def test_a_logged_in_user_is_redirected_from_login() -> None:
    user = User.objects.create_user(phone=PHONE)
    client = Client()
    client.force_login(user)

    response = client.get(reverse("account_login"))

    assert response.status_code == 302
    assert response["Location"] == reverse("home")


# ---------------------------------------------------------------------------
# Rate limiting, CSRF and the header
# ---------------------------------------------------------------------------
def test_a_rate_limited_request_shows_the_message_and_sends_nothing() -> None:
    client = Client()
    _start(client)

    response = client.post(reverse("account_login"), {"phone": PHONE})

    assert response.status_code == 200
    assert "Too many codes requested for this number. Please try again later." in (
        response.content.decode()
    )
    assert len(SENT) == 1


def test_csrf_is_enforced_on_the_login_post() -> None:
    client = Client(enforce_csrf_checks=True)

    response = client.post(reverse("account_login"), {"phone": PHONE})

    assert response.status_code == 403
    assert SENT == []


def test_header_shows_login_when_logged_out_and_logout_when_logged_in() -> None:
    client = Client()

    page = client.get(reverse("home")).content.decode()
    assert 'href="/account/login/"' in page
    assert 'action="/account/logout/"' not in page

    user = User.objects.create_user(phone=PHONE)
    client.force_login(user)
    page = client.get(reverse("home")).content.decode()
    assert 'action="/account/logout/"' in page
    assert 'href="/account/login/"' not in page
