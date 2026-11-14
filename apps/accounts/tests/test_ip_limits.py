"""Per-IP limits on the login-side forms (D89).

The counters live in the cache, which locmem keeps across a database rollback,
so every test clears it on the way in and on the way out. A test that wants a
full counter fills it with ``record``, which is exactly what a real request
calls, instead of posting the form twenty times.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.accounts.auth_presentation import (
    FORGOT_SENT_MESSAGE,
    IP_LIMITED_MESSAGE,
)
from apps.accounts.models import User
from apps.accounts.views import SESSION_PHONE_KEY
from apps.core.ratelimit import (
    OTP_REQUESTS_PER_HOUR,
    PASSWORD_FAILURES_PER_HOUR,
    SCOPE_LOGIN,
    SCOPE_OTP,
    SCOPE_SIGNUP,
    SIGNUPS_PER_HOUR,
    is_limited,
    record,
)
from apps.notifications.models import NotificationLog
from apps.notifications.sms import SmsBackend

pytestmark = pytest.mark.django_db

# The address the Django test client sends from, unless a test says otherwise.
VISITOR = "127.0.0.1"
OTHER_VISITOR = "203.0.113.8"
PHONE = "+919876543210"
PASSWORD = "StrongPass1"

SENT: list[tuple[str, str]] = []


class RecordingSmsBackend(SmsBackend):
    """Keep the codes in memory instead of sending them."""

    def send_otp(self, phone: str, code: str) -> None:
        SENT.append((phone, code))


@pytest.fixture(autouse=True)
def _clean_state(settings):
    """Point SMS at the fake, and start and end every test with an empty cache."""
    settings.SMS_BACKEND = f"{__name__}.RecordingSmsBackend"
    cache.clear()
    SENT.clear()
    yield
    SENT.clear()
    cache.clear()


def _phone(index: int) -> str:
    """A valid Indian number that is different for every index."""
    return f"+9198765{index:05d}"


# ``*`` makes ``ip`` keyword-only, so a caller cannot pass it by position.
def _fill(times: int, scope: str, *, ip: str = VISITOR) -> None:
    """Count ``times`` tries for one address, the way real requests would."""
    request = RequestFactory().post("/", REMOTE_ADDR=ip)
    for _index in range(times):
        record(request, scope)


def _signup_data(index: int) -> dict[str, str]:
    """A sign-up form that passes every rule."""
    return {
        "phone": _phone(index),
        "full_name": "Asha Sharma",
        "email": f"asha-{index}@example.com",
        "password1": "kabuli-chana-42",
        "password2": "kabuli-chana-42",
    }


# ---------------------------------------------------------------------------
# The code form
# ---------------------------------------------------------------------------
def test_otp_request_blocked_after_20_from_one_ip() -> None:
    client = Client()
    for index in range(OTP_REQUESTS_PER_HOUR):
        assert client.post(reverse("account_login"), {"phone": _phone(index)}).status_code == 302

    response = client.post(reverse("account_login"), {"phone": PHONE})

    assert response.status_code == 200
    assert str(IP_LIMITED_MESSAGE) in response.content.decode()
    # The twentieth code went out; the twenty-first did not.
    assert len(SENT) == OTP_REQUESTS_PER_HOUR


def test_otp_block_does_not_call_request_code() -> None:
    _fill(OTP_REQUESTS_PER_HOUR, SCOPE_OTP)

    with patch("apps.accounts.views.request_code") as request_code_mock:
        response = Client().post(reverse("account_login"), {"phone": PHONE})

    assert str(IP_LIMITED_MESSAGE) in response.content.decode()
    request_code_mock.assert_not_called()


def test_otp_other_ip_not_blocked() -> None:
    _fill(OTP_REQUESTS_PER_HOUR, SCOPE_OTP)

    response = Client().post(
        reverse("account_login"),
        {"phone": PHONE},
        REMOTE_ADDR=OTHER_VISITOR,
    )

    assert response.status_code == 302
    assert len(SENT) == 1


def test_verification_resend_is_limited() -> None:
    user = User.objects.create_user(phone=PHONE)
    client = Client()
    client.force_login(user)
    _fill(OTP_REQUESTS_PER_HOUR, SCOPE_OTP)

    response = client.post(reverse("account_verify_phone"), {"action": "send"})

    assert response.status_code == 302
    assert SENT == []
    stored = [str(message) for message in get_messages(response.wsgi_request)]
    assert str(IP_LIMITED_MESSAGE) in stored


def test_password_reset_limited_gives_same_neutral_page() -> None:
    User.objects.create_user(phone=PHONE)
    _fill(OTP_REQUESTS_PER_HOUR, SCOPE_OTP)
    client = Client()

    response = client.post(reverse("account_forgot_password"), {"identifier": PHONE})

    # The same page and the same redirect as any other request, and nothing
    # sent: a limit must not say whether the account exists.
    assert response.status_code == 302
    assert response["Location"] == reverse("account_reset_password")
    stored = [str(message) for message in get_messages(response.wsgi_request)]
    assert str(FORGOT_SENT_MESSAGE) in stored
    assert SENT == []
    assert NotificationLog.objects.count() == 0


# ---------------------------------------------------------------------------
# Sign-up
# ---------------------------------------------------------------------------
def test_signup_blocked_after_20_valid_posts() -> None:
    # Each counted try stands for one sign-up form that passed validation.
    _fill(SIGNUPS_PER_HOUR, SCOPE_SIGNUP)

    response = Client().post(reverse("account_signup"), _signup_data(1))

    assert response.status_code == 200
    assert str(IP_LIMITED_MESSAGE) in response.content.decode()
    assert not User.objects.filter(phone=_phone(1)).exists()


def test_invalid_signup_posts_do_not_count() -> None:
    client = Client()
    for _index in range(SIGNUPS_PER_HOUR + 5):
        response = client.post(reverse("account_signup"), {"phone": "12345"})
        assert response.status_code == 200

    # Not one of those counted, so this one is still allowed.
    response = client.post(reverse("account_signup"), _signup_data(7))

    assert response.status_code == 302
    assert User.objects.filter(phone=_phone(7)).exists()


# ---------------------------------------------------------------------------
# The password form
# ---------------------------------------------------------------------------
def _password_login(client: Client, identifier: str, password: str, **extra):
    data = {"method": "password", "identifier": identifier, "password": password}
    data.update(extra)
    return client.post(reverse("account_login"), data)


def test_password_login_blocked_after_30_failures() -> None:
    client = Client()
    for index in range(PASSWORD_FAILURES_PER_HOUR):
        _password_login(client, _phone(index), "wrong-password-1")

    response = _password_login(client, PHONE, "wrong-password-1")

    assert response.status_code == 200
    assert str(IP_LIMITED_MESSAGE) in response.content.decode()


def test_successful_password_login_does_not_count() -> None:
    User.objects.create_user(phone=PHONE, password=PASSWORD)
    client = Client()

    response = _password_login(client, PHONE, PASSWORD)

    assert response.status_code == 302
    request = RequestFactory().post("/", REMOTE_ADDR="127.0.0.1")
    assert is_limited(request, SCOPE_LOGIN, 1) is False


def test_failed_password_login_counts() -> None:
    client = Client()

    _password_login(client, PHONE, "wrong-password-1")

    request = RequestFactory().post("/", REMOTE_ADDR="127.0.0.1")
    assert is_limited(request, SCOPE_LOGIN, 1) is True


def test_message_shown_when_limited() -> None:
    _fill(OTP_REQUESTS_PER_HOUR, SCOPE_OTP)
    client = Client()

    response = client.post(reverse("account_login"), {"phone": PHONE})

    assert str(IP_LIMITED_MESSAGE) in response.content.decode()
    # No code was requested, so the verify page has not been prepared.
    assert SESSION_PHONE_KEY not in client.session


# ---------------------------------------------------------------------------
# The staff login is not touched
# ---------------------------------------------------------------------------
def test_staff_admin_login_not_limited() -> None:
    User.objects.create_user(phone=PHONE, password=PASSWORD, is_staff=True, is_superuser=True)
    _fill(PASSWORD_FAILURES_PER_HOUR, SCOPE_LOGIN)
    _fill(OTP_REQUESTS_PER_HOUR, SCOPE_OTP)

    response = Client().post(
        "/admin/login/",
        {"username": PHONE, "password": PASSWORD, "next": "/admin/"},
    )

    # A correct password on the staff page still logs in: the limit belongs to
    # the shop's own forms (D89).
    assert response.status_code == 302
