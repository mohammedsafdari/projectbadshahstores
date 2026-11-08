"""Stand-in SMS backends for the order notice tests (Slice 38a, D69)."""

from __future__ import annotations

from apps.notifications.sms import SmsBackend


class RecordingSmsBackend(SmsBackend):
    """Record every free-text message instead of sending it.

    The list is on the class, not on an instance, because ``get_sms_backend``
    builds a fresh backend on every call: a class list is the one place the
    test can read after the call.
    """

    messages: list[tuple[str, str]] = []

    def send_otp(self, phone: str, code: str) -> None:
        """Do nothing: order notices never send login codes."""
        return

    def send_message(self, phone: str, text: str) -> None:
        RecordingSmsBackend.messages.append((phone, text))


class FailingSmsBackend(SmsBackend):
    """Raise on every free-text message, to prove the failure is swallowed."""

    def send_otp(self, phone: str, code: str) -> None:
        """Do nothing: this backend only exists to fail a message."""
        return

    def send_message(self, phone: str, text: str) -> None:
        raise RuntimeError("boom")
