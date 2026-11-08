"""Verification codes sent by email (D71).

A failure here must never break the page, so every error is swallowed and
logged. The log line never carries the code, the address or a phone number.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)

# The subject is fixed and never contains the code (D71).
SUBJECT = "Badshah Stores: your verification code"


def email_body(code: str) -> str:
    """The plain body of one verification email."""
    return (
        f"Your Badshah Stores verification code is {code}.\n"
        "It is valid for 5 minutes.\n"
        "Do not share this code with anyone.\n"
    )


def send_email_code(email: str, code: str) -> None:
    """Send one verification code by email, and never raise (D71).

    It always sends: it is not controlled by ``ORDER_EMAIL_ENABLED``. In
    development Django's console email backend prints the message.
    """
    try:
        send_mail(SUBJECT, email_body(code), settings.DEFAULT_FROM_EMAIL, [email])
    except Exception:
        # Only the fixed text: never the code, the address or the phone.
        logger.exception("Verification email failed")
