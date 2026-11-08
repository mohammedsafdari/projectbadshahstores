"""OTP login takeover and the email address it removes (Slice 38f, D71, D72).

The takeover proof is the phone, so an email that somebody else may have typed
must go with the password. These tests read the stored values back, because the
service now writes four fields in one ``update_fields`` call.
"""

from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.passwords import register_customer
from apps.accounts.verification_presentation import PASSWORD_RESET_FOR_SAFETY_MESSAGE
from apps.notifications.sms import SmsBackend

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
OTHER_PHONE = "+919876543211"
EMAIL = "asha@example.com"

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


def _start(client: Client) -> None:
    client.post(reverse("account_login"), {"phone": PHONE})


def _verify(client: Client):
    return client.post(reverse("account_verify"), {"code": SENT[-1][1]})


def test_otp_takeover_removes_the_email_address() -> None:
    # An account that was never verified: the password is unusable already, so
    # only the email can be taken away.
    user = User.objects.create_user(phone=PHONE, email=EMAIL)
    assert user.has_usable_password() is False
    client = Client()
    _start(client)

    _verify(client)

    user.refresh_from_db()
    assert user.phone_verified_at is not None
    assert user.email is None
    assert user.email_verified_at is None


def test_otp_takeover_message_is_shown_when_only_the_email_was_removed() -> None:
    User.objects.create_user(phone=PHONE, email=EMAIL)
    client = Client()
    _start(client)

    response = _verify(client)

    stored = [str(message) for message in get_messages(response.wsgi_request)]
    assert str(PASSWORD_RESET_FOR_SAFETY_MESSAGE) in stored


def test_takeover_frees_the_email_for_a_new_signup() -> None:
    User.objects.create_user(phone=PHONE, email=EMAIL)
    client = Client()
    _start(client)
    _verify(client)

    # The same address may now start a fresh account on another phone.
    new_user = register_customer(
        phone=OTHER_PHONE,
        email=EMAIL,
        full_name="Asha Rao",
        password="StrongPass1",
    )

    assert new_user.email == EMAIL


def test_takeover_of_a_verified_account_keeps_the_email() -> None:
    user = User.objects.create_user(phone=PHONE, email=EMAIL)
    user.phone_verified_at = timezone.now()
    user.email_verified_at = timezone.now()
    user.save(update_fields=["phone_verified_at", "email_verified_at"])
    verified_at = user.email_verified_at
    client = Client()
    _start(client)

    _verify(client)

    user.refresh_from_db()
    assert user.email == EMAIL
    assert user.email_verified_at == verified_at
