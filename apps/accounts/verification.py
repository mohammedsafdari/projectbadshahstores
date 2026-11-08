"""Phone and email verification, and the OTP login takeover (D71).

The rules stay here; the OTP module still owns the code, the HMAC and the rate
limits, and the pages own the wording. A verification only ever sets a
timestamp: it never touches the password.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from apps.accounts.models import OtpChallenge, User
from apps.accounts.otp import OtpRequestResult, request_code, verify_code
from apps.notifications.email_codes import send_email_code

logger = logging.getLogger(__name__)

# Returned by request_email_code when the account has no email address.
REASON_NO_EMAIL = "no_email"


def request_phone_code(user: User) -> OtpRequestResult:
    """Send a phone verification code by SMS to the account's own phone."""
    return request_code(user.phone, purpose=OtpChallenge.Purpose.PHONE_VERIFY)


def request_email_code(user: User) -> OtpRequestResult:
    """Send an email verification code, or refuse when there is no address.

    Challenges stay keyed by the account's phone whatever the channel (D71).
    The email is read once, before the call, so the sender always uses the
    address that was checked.
    """
    email = user.email
    if not email:
        return OtpRequestResult(phone=user.phone, sent=False, reason=REASON_NO_EMAIL)
    return request_code(
        user.phone,
        purpose=OtpChallenge.Purpose.EMAIL_VERIFY,
        send=lambda code: send_email_code(email, code),
    )


def confirm_phone_code(user: User, code: str) -> bool:
    """Check a phone verification code and, on success, record the time.

    The queryset update matches only a still-blank field, so a second
    verification keeps the first time. Nothing else on the account changes.
    """
    result = verify_code(user.phone, code, purpose=OtpChallenge.Purpose.PHONE_VERIFY)
    if not result.ok:
        return False
    User.objects.filter(pk=user.pk, phone_verified_at__isnull=True).update(
        phone_verified_at=timezone.now()
    )
    # The update() above wrote straight to the database, so the object the view
    # holds must be re-read to show the new value.
    user.refresh_from_db(fields=["phone_verified_at"])
    return True


def confirm_email_code(user: User, code: str) -> bool:
    """Check an email verification code and, on success, record the time."""
    result = verify_code(user.phone, code, purpose=OtpChallenge.Purpose.EMAIL_VERIFY)
    if not result.ok:
        return False
    User.objects.filter(pk=user.pk, email_verified_at__isnull=True).update(
        email_verified_at=timezone.now()
    )
    user.refresh_from_db(fields=["email_verified_at"])
    return True


def claim_account_by_otp_login(user: User) -> bool:
    """Prove the phone on an OTP login and take over an unverified account.

    If the phone was never verified, the account is taken over: the phone is
    marked verified, the password is cleared and any email verification is
    dropped, because somebody may have signed up with another person's number.
    Returns True only when a usable password was cleared, so the view can tell
    the customer. A second OTP login does nothing and returns False.
    """
    if user.phone_verified_at is not None:
        return False
    had_usable_password = user.has_usable_password()
    user.phone_verified_at = timezone.now()
    user.set_unusable_password()
    user.email_verified_at = None
    user.save(update_fields=["phone_verified_at", "password", "email_verified_at"])
    # The id only: never the phone or the email address.
    logger.info("OTP login took over an unverified account: user_id=%s", user.pk)
    return had_usable_password
