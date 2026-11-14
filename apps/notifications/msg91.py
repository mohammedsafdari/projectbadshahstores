"""The MSG91 Flow API SMS backend (D88).

This is the only file that knows MSG91's URLs and JSON. Everything the rest of
the project sends arrives as one DLT template plus its variables, because a
free-text SMS is not allowed in India. The calls use the standard library, so
the project needs no extra dependency; ``urllib.request`` is used exactly the
way the payment gateway module calls its own provider.

Nothing here ever logs or prints the auth key, the phone number or the
variables, and no error message carries them either.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.notifications.sms import SmsBackend

# ---------------------------------------------------------------------------
# Provider details. check against MSG91's current Flow API documentation
# before the first real send (D88).
# ---------------------------------------------------------------------------
FLOW_URL = "https://control.msg91.com/api/v5/flow/"
AUTH_HEADER = "authkey"
ACCEPT_HEADER = "accept"
CONTENT_TYPE_HEADER = "content-type"
JSON_CONTENT_TYPE = "application/json"
TEMPLATE_ID_KEY = "template_id"
RECIPIENTS_KEY = "recipients"
MOBILES_KEY = "mobiles"
REQUEST_ID_KEY = "request_id"
REPLY_TYPE_KEY = "type"
ERROR_REPLY_TYPE = "error"
# The template key and the variable name a login code uses.
OTP_TEMPLATE_KEY = "OTP"
OTP_VARIABLE_KEY = "OTP"

# How much of a provider error message is safe and useful to quote.
ERROR_TEXT_LIMIT = 120


class Msg91Error(RuntimeError):
    """Raised when MSG91 refuses the message or cannot be reached (D88)."""


def _auth_key() -> str:
    """The MSG91 auth key, or a clear refusal when it is missing."""
    key = str(getattr(settings, "MSG91_AUTH_KEY", "") or "").strip()
    if not key:
        raise ImproperlyConfigured("MSG91_AUTH_KEY is not configured.")
    return key


def _timeout() -> int:
    """The request timeout in seconds, always a whole number."""
    return int(getattr(settings, "MSG91_TIMEOUT_SECONDS", 10))


def _template_id(event: str) -> str:
    """The registered template id for one event, or a refusal."""
    # getattr with a default keeps a test that never set the name working.
    templates = getattr(settings, "MSG91_TEMPLATE_IDS", {}) or {}
    template_id = ""
    if isinstance(templates, Mapping):
        template_id = str(templates.get(event, "") or "").strip()
    if not template_id:
        # The event only: the id itself is never printed.
        raise ImproperlyConfigured(f"No MSG91 template id is configured for {event}.")
    return template_id


def _without_plus(phone: str) -> str:
    """MSG91 wants 91XXXXXXXXXX, so the leading + is dropped."""
    # removeprefix removes the prefix only when it is there, unlike [1:].
    return phone.strip().removeprefix("+")


def _short_text(value: object) -> str:
    """At most a few characters of a provider message, with no personal data."""
    return str(value)[:ERROR_TEXT_LIMIT]


class Msg91SmsBackend(SmsBackend):
    """SMS through one MSG91 Flow template per event (D88)."""

    def send_otp(self, phone: str, code: str) -> None:
        """Send one login code with the OTP template."""
        self._send(OTP_TEMPLATE_KEY, phone, {OTP_VARIABLE_KEY: code})

    def send_message(self, phone: str, text: str) -> None:
        """Free text is not allowed on a DLT-registered number."""
        raise NotImplementedError("DLT needs a registered template; use send_notice")

    def send_notice(
        self,
        phone: str,
        event: str,
        variables: dict[str, str],
        text: str,
    ) -> str:
        """Send one notice with the template registered for this event.

        ``text`` is unused: MSG91 sends the registered template, not free text.
        The reply's request id is returned when it has one.
        """
        return self._send(event, phone, variables)

    def _send(self, template_key: str, phone: str, variables: dict[str, str]) -> str:
        """POST one Flow request and return the provider's request id."""
        auth_key = _auth_key()
        template_id = _template_id(template_key)
        recipient = {MOBILES_KEY: _without_plus(phone), **variables}
        payload = {TEMPLATE_ID_KEY: template_id, RECIPIENTS_KEY: [recipient]}
        # The URL is a fixed provider endpoint, never user input.
        request = Request(
            FLOW_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                AUTH_HEADER: auth_key,
                ACCEPT_HEADER: JSON_CONTENT_TYPE,
                CONTENT_TYPE_HEADER: JSON_CONTENT_TYPE,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=_timeout()) as response:  # noqa: S310
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            # The status and the event only: never the key, the phone or a body.
            raise Msg91Error(
                f"MSG91 refused the {template_key} message (HTTP {error.code})."
            ) from error
        except Exception as error:
            raise Msg91Error(f"MSG91 did not answer the {template_key} message.") from error
        if not isinstance(data, dict):
            raise Msg91Error(f"MSG91 returned an unexpected reply for {template_key}.")
        if str(data.get(REPLY_TYPE_KEY, "")).lower() == ERROR_REPLY_TYPE:
            # The provider's own words are short and carry no personal data,
            # but only a clipped copy is kept in the exception.
            raise Msg91Error(
                f"MSG91 reported an error for the {template_key} message: "
                f"{_short_text(data.get('message', ''))}"
            )
        return str(data.get(REQUEST_ID_KEY, "") or "")
