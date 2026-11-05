"""Tests for the SMS backend interface (Slice 30, D59)."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.notifications.sms import (
    ConsoleSmsBackend,
    SmsBackend,
    get_sms_backend,
)

PHONE = "+919876543210"
CODE = "123456"


class RecordingSmsBackend(SmsBackend):
    """A stand-in backend that records calls instead of sending anything."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_otp(self, phone: str, code: str) -> None:
        self.sent.append((phone, code))


def test_console_backend_prints_the_code_in_debug(settings, capsys) -> None:
    settings.DEBUG = True

    ConsoleSmsBackend().send_otp(PHONE, CODE)

    out = capsys.readouterr().out
    assert "[DEV SMS]" in out
    assert CODE in out


def test_console_backend_masks_the_phone(settings, capsys) -> None:
    settings.DEBUG = True

    ConsoleSmsBackend().send_otp(PHONE, CODE)

    out = capsys.readouterr().out
    assert "+91******3210" in out
    assert PHONE not in out


def test_console_backend_refuses_when_debug_is_off(settings) -> None:
    settings.DEBUG = False

    with pytest.raises(ImproperlyConfigured):
        ConsoleSmsBackend().send_otp(PHONE, CODE)


def test_get_sms_backend_loads_the_setting(settings) -> None:
    settings.SMS_BACKEND = f"{__name__}.RecordingSmsBackend"

    backend = get_sms_backend()

    assert isinstance(backend, RecordingSmsBackend)
