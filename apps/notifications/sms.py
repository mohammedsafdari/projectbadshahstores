"""SMS sending behind a small interface (D25, D59).

The rest of the project never talks to an SMS provider directly: it asks for a
backend through ``get_sms_backend()`` and calls ``send_otp``. A real MSG91
backend can replace the console one later without touching the OTP service.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import cast

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

DEFAULT_SMS_BACKEND = "apps.notifications.sms.ConsoleSmsBackend"


class SmsBackend(ABC):
    """What the project needs from an SMS provider."""

    @abstractmethod
    def send_otp(self, phone: str, code: str) -> None:
        """Send one login code to one phone number."""

    # Deliberately not abstract: a backend written for login codes only still
    # works, and order notices fail cleanly if it cannot send free text (D69).
    def send_message(self, phone: str, text: str) -> None:
        """Send one free-text message, such as an order notice."""
        raise NotImplementedError("This SMS backend cannot send free text.")


class ConsoleSmsBackend(SmsBackend):
    """Print the code to stdout during development only.

    It never uses the logging module, because a log file is easy to leave on
    and a printed line is easy to see while testing. In production it raises,
    so a missing SMS_BACKEND setting cannot silently drop real messages.
    """

    def send_otp(self, phone: str, code: str) -> None:
        if not settings.DEBUG:
            raise ImproperlyConfigured(
                "ConsoleSmsBackend is for development only. "
                "Set SMS_BACKEND to a real SMS backend in production."
            )
        print(f"[DEV SMS] to {_mask_phone(phone)}: code {code}")

    def send_message(self, phone: str, text: str) -> None:
        """Print a free-text order message, in development only (D69)."""
        if not settings.DEBUG:
            raise ImproperlyConfigured(
                "ConsoleSmsBackend is for development only. "
                "Set SMS_BACKEND to a real SMS backend in production."
            )
        print(f"[DEV SMS] to {_mask_phone(phone)}: {text}")


def _mask_phone(phone: str) -> str:
    """Keep the country code and the last four digits: +91******1234."""
    # Slicing a string copies the parts: [:3] is "+91", [-4:] is the last four.
    return f"{phone[:3]}******{phone[-4:]}"


def get_sms_backend() -> SmsBackend:
    """Build the backend named in settings, or the console one by default."""
    path = getattr(settings, "SMS_BACKEND", DEFAULT_SMS_BACKEND)
    backend_class = import_string(path)
    # A cast only helps mypy; import_string returns Any at run time.
    return cast(SmsBackend, backend_class())
