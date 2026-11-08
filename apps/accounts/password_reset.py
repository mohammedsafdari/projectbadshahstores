"""Forgot-password and reset rules (D73).

The reset flow reuses the one-time codes: ``PASSWORD_RESET`` is the purpose, so
a login code can never reset a password. Only customer accounts are found, and
the service never logs anyone in. The view stores only the phone and the channel
in the session.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import OtpChallenge
from apps.accounts.otp import request_code, verify_code
from apps.accounts.passwords import clear_login_failures, find_customer
from apps.core.phone import normalise_phone
from apps.notifications.email_codes import send_email_code

CHANNEL_PHONE = "phone"
CHANNEL_EMAIL = "email"

# The two session keys the reset page reads. Nothing else is stored (D73).
SESSION_RESET_PHONE = "account_reset_phone"
SESSION_RESET_CHANNEL = "account_reset_channel"

# A syntactically valid number used only to keep the cost of an unknown account
# similar to a real check; no code can ever match it.
DUMMY_PHONE = "+910000000000"


@dataclass(frozen=True)
class ResetRequest:
    """Where the code went, and which channel the reset page must use."""

    phone: str
    channel: str


@dataclass(frozen=True)
class ResetOutcome:
    """Whether the reset worked, and whether an email address was removed."""

    ok: bool
    email_removed: bool = False


def request_password_reset(identifier: str) -> ResetRequest:
    """Send a reset code by phone or email, in the same way for every account.

    A known account gets a code; an unknown, staff, inactive or rate-limited
    request sends nothing and returns the same shape. The customer never learns
    which case applied.
    """
    text = identifier.strip()
    is_email = "@" in text
    channel = CHANNEL_EMAIL if is_email else CHANNEL_PHONE
    user = find_customer(text)

    if user is None:
        # A typed phone is returned normalised even when nobody owns it; an
        # unknown email has no phone to keep.
        phone = ""
        if not is_email:
            try:
                phone = normalise_phone(text)
            except ValueError:
                phone = ""
        return ResetRequest(phone=phone, channel=channel)

    if is_email:
        # Read the address before the call, so the sender always uses the one
        # that was checked.
        email = user.email or ""
        request_code(
            user.phone,
            purpose=OtpChallenge.Purpose.PASSWORD_RESET,
            send=lambda code: send_email_code(email, code),
        )
    else:
        request_code(user.phone, purpose=OtpChallenge.Purpose.PASSWORD_RESET)
    return ResetRequest(phone=user.phone, channel=channel)


def _run_dummy_verify(phone: str, code: str) -> None:
    """Do the same code check for an unknown account, then ignore the answer."""
    target = phone or DUMMY_PHONE
    try:
        verify_code(target, code, purpose=OtpChallenge.Purpose.PASSWORD_RESET)
    except ValueError:
        # An unusable value costs about the same as a normalised phone that
        # owns no challenge; a reset must never raise for it.
        return


def confirm_password_reset(
    *,
    phone: str,
    channel: str,
    code: str,
    new_password: str,
) -> ResetOutcome:
    """Check the reset code and, on success, set the new password.

    A reset by phone proves the phone, so an account that was never verified is
    taken over exactly like an OTP login (D71): the email address and its
    verification are removed. A reset by email proves the email only, so the
    phone and the password are the only fields touched.
    """
    user = find_customer(phone) if phone else None
    if user is None:
        _run_dummy_verify(phone, code)
        return ResetOutcome(ok=False)

    result = verify_code(phone, code, purpose=OtpChallenge.Purpose.PASSWORD_RESET)
    if not result.ok:
        return ResetOutcome(ok=False)

    email_removed = False
    # One transaction: either the new password and the verification change
    # together, or neither does. ``verify_code`` already locked the challenge,
    # so no second row lock is needed here (D73).
    with transaction.atomic():
        user.set_password(new_password)
        update_fields = ["password", "updated_at"]
        if channel == CHANNEL_PHONE:
            if user.phone_verified_at is None:
                user.phone_verified_at = timezone.now()
                update_fields.append("phone_verified_at")
                if user.email:
                    user.email = None
                    user.email_verified_at = None
                    update_fields.extend(["email", "email_verified_at"])
                    email_removed = True
        else:
            if user.email_verified_at is None:
                user.email_verified_at = timezone.now()
                update_fields.append("email_verified_at")
        user.save(update_fields=update_fields)

    # A successful reset proves the account owner, so old wrong tries must not
    # keep the account locked.
    clear_login_failures(user)
    return ResetOutcome(ok=True, email_removed=email_removed)
