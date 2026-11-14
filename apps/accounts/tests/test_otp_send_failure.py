"""A code the SMS provider refused is shown, and its challenge removed (D89).

The broken backend raises inside ``send_otp``; ``deliver`` turns that into a
FAILED log row, and the OTP service must then tell the page the truth.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.accounts.auth_presentation import FORGOT_SENT_MESSAGE, SEND_FAILED_MESSAGE
from apps.accounts.models import OtpChallenge
from apps.accounts.otp import REASON_OK, REASON_SEND_FAILED, request_code
from apps.accounts.views import SESSION_PHONE_KEY
from apps.notifications.models import NotificationLog
from apps.notifications.sms import SmsBackend
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
BROKEN_BACKEND = f"{__name__}.BrokenSmsBackend"

# Every code the recording backend was asked to send, oldest first.
SENT: list[tuple[str, str]] = []


class BrokenSmsBackend(SmsBackend):
    """A provider that refuses every code, the way a bad key would."""

    def send_otp(self, phone: str, code: str) -> None:
        raise RuntimeError("provider down")


class RecordingSmsBackend(SmsBackend):
    """Keep the codes in memory instead of sending them."""

    def send_otp(self, phone: str, code: str) -> None:
        SENT.append((phone, code))


@pytest.fixture(autouse=True)
def _clean_state():
    """Empty the recorded codes and the cache around every test.

    The cache holds the per-IP counters, and locmem survives a rollback.
    """
    cache.clear()
    SENT.clear()
    yield
    SENT.clear()
    cache.clear()


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------
def test_failed_sms_returns_send_failed_and_deletes_challenge(settings) -> None:
    settings.SMS_BACKEND = BROKEN_BACKEND

    result = request_code(PHONE)

    assert result.sent is False
    assert result.reason == REASON_SEND_FAILED
    # The row is gone, so the next request is not refused as "too soon".
    assert OtpChallenge.objects.filter(phone=PHONE).count() == 0
    # The attempt is still in the send log.
    log = NotificationLog.objects.get()
    assert log.status == NotificationLog.Status.FAILED


def test_new_request_after_send_failure_is_not_too_soon(settings) -> None:
    settings.SMS_BACKEND = BROKEN_BACKEND
    request_code(PHONE)

    settings.SMS_BACKEND = f"{__name__}.RecordingSmsBackend"
    result = request_code(PHONE)

    assert result.sent is True
    assert result.reason == REASON_OK
    assert len(SENT) == 1


def test_log_failure_still_counts_as_sent() -> None:
    # ``deliver`` returns None when the log row itself could not be written;
    # the message was still handed to the provider, so it counts as sent.
    with patch("apps.accounts.otp.deliver", return_value=None):
        result = request_code(PHONE)

    assert result.sent is True
    assert result.reason == REASON_OK
    assert OtpChallenge.objects.filter(phone=PHONE).count() == 1


def test_custom_send_branch_is_unchanged() -> None:
    codes: list[str] = []

    with patch("apps.accounts.otp.deliver") as deliver_mock:
        result = request_code(PHONE, send=codes.append)

    assert result.sent is True
    assert len(codes) == 1
    # A caller that sends the code itself never reaches the SMS path.
    deliver_mock.assert_not_called()
    assert OtpChallenge.objects.filter(phone=PHONE).count() == 1


# ---------------------------------------------------------------------------
# The pages
# ---------------------------------------------------------------------------
def test_login_page_shows_send_failed_message(settings) -> None:
    settings.SMS_BACKEND = BROKEN_BACKEND
    client = Client()

    response = client.post(reverse("account_login"), {"phone": PHONE})

    assert response.status_code == 200
    assert str(SEND_FAILED_MESSAGE) in response.content.decode()
    assert SESSION_PHONE_KEY not in client.session


def test_verification_resend_shows_send_failed_message(settings) -> None:
    settings.SMS_BACKEND = BROKEN_BACKEND
    user = UserFactory(phone=PHONE, phone_verified_at=None)
    client = Client()
    client.force_login(user)

    response = client.post(reverse("account_verify_phone"), {"action": "send"})

    assert response.status_code == 302
    stored = [str(message) for message in get_messages(response.wsgi_request)]
    assert str(SEND_FAILED_MESSAGE) in stored


def test_password_reset_ignores_send_failure(settings) -> None:
    settings.SMS_BACKEND = BROKEN_BACKEND
    UserFactory(phone=PHONE)
    client = Client()

    response = client.post(reverse("account_forgot_password"), {"identifier": PHONE})
    followed = client.get(response["Location"])

    # The same neutral page as an account that was found, and the failure is
    # never named: D59 keeps known and unknown accounts alike.
    assert response.status_code == 302
    assert response["Location"] == reverse("account_reset_password")
    assert str(FORGOT_SENT_MESSAGE) in followed.content.decode()
    assert str(SEND_FAILED_MESSAGE) not in followed.content.decode()
