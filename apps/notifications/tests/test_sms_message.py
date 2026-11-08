"""Tests for the free-text SMS method (Slice 38a, D69)."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.notifications.sms import ConsoleSmsBackend, SmsBackend

PHONE = "+919876543210"
TEXT = "Badshah Stores: Order BS/26-27/000001 is placed."


class OtpOnlyBackend(SmsBackend):
    """A backend that predates free-text messages and only sends login codes."""

    def send_otp(self, phone: str, code: str) -> None:
        """Do nothing; this backend exists to prove send_message is optional."""
        return


def test_console_send_message_prints_masked_phone_and_text_in_debug(settings, capsys) -> None:
    settings.DEBUG = True

    ConsoleSmsBackend().send_message(PHONE, TEXT)

    out = capsys.readouterr().out
    assert "[DEV SMS]" in out
    assert "+91******3210" in out
    assert PHONE not in out
    assert TEXT in out


def test_console_send_message_raises_when_debug_is_false(settings) -> None:
    settings.DEBUG = False

    with pytest.raises(ImproperlyConfigured):
        ConsoleSmsBackend().send_message(PHONE, TEXT)


def test_base_backend_send_message_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        OtpOnlyBackend().send_message(PHONE, TEXT)
