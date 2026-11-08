"""Verification wording: one place for every sentence a customer reads (D71).

Views and templates never spell these words out themselves. The masking
helpers keep the full phone and email off the page.
"""

from __future__ import annotations

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User

PHONE_TITLE = _("Verify your mobile number")
PHONE_HEADING = _("Verify your mobile number")
EMAIL_TITLE = _("Verify your email address")
EMAIL_HEADING = _("Verify your email address")
CONTACT_PREFIX = _("We will send the code to")

SEND_CODE_BUTTON = _("Send me a code")
CONFIRM_BUTTON = _("Verify")
CODE_LABEL = _("Code")

VERIFIED_MESSAGE = _("Verified. Thank you.")
CODE_SENT_MESSAGE = _("We have sent you a code.")
CODE_ERROR_MESSAGE = _("That code did not work. Ask for a new one.")
WAIT_MESSAGE = _("Please wait a little before asking for another code.")
NO_EMAIL_MESSAGE = _("There is no email address on your account yet.")
PASSWORD_RESET_FOR_SAFETY_MESSAGE = _(
    "For your safety we reset the password and removed the email address on this account, "
    "because its mobile number had not been verified. You can set them again later."
)

BANNER_HEADING = _("Please verify your details so we can deliver your order.")
BANNER_PHONE = _("Verify your mobile number")
BANNER_EMAIL = _("Verify your email address")


def mask_phone(phone: str) -> str:
    """Keep the country code and the last four digits: +91******3210."""
    # Slicing a string copies the parts: [:3] is "+91", [-4:] is the last four.
    return f"{phone[:3]}******{phone[-4:]}"


def mask_email(email: str) -> str:
    """Keep the first letter and the domain: a***@example.com.

    A value without both a name and a domain never reaches a page in full.
    """
    local, separator, domain = email.partition("@")
    if not local or not separator or not domain:
        return "***"
    return f"{local[0]}***@{domain}"


def pending_verifications(user: User) -> list[dict[str, str]]:
    """What this customer still has to verify before the first delivery.

    One dict per missing item, so the banner template only prints values. A
    customer without an email is never asked for one (D71).
    """
    items: list[dict[str, str]] = []
    if user.phone_verified_at is None:
        items.append(
            {
                "kind": "phone",
                "label": str(BANNER_PHONE),
                "url": reverse("account_verify_phone"),
            }
        )
    if user.email and user.email_verified_at is None:
        items.append(
            {
                "kind": "email",
                "label": str(BANNER_EMAIL),
                "url": reverse("account_verify_email"),
            }
        )
    return items


def verification_text() -> dict[str, str]:
    """Every fixed sentence the verification pages and the banner print."""
    return {
        "phone_title": str(PHONE_TITLE),
        "phone_heading": str(PHONE_HEADING),
        "email_title": str(EMAIL_TITLE),
        "email_heading": str(EMAIL_HEADING),
        "contact_prefix": str(CONTACT_PREFIX),
        "send_code_button": str(SEND_CODE_BUTTON),
        "confirm_button": str(CONFIRM_BUTTON),
        "code_label": str(CODE_LABEL),
        "verified_message": str(VERIFIED_MESSAGE),
        "no_email_message": str(NO_EMAIL_MESSAGE),
        "banner_heading": str(BANNER_HEADING),
        "banner_phone": str(BANNER_PHONE),
        "banner_email": str(BANNER_EMAIL),
    }
