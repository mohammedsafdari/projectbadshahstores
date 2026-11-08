"""Phone login codes: issue, check and clean up (D17, D59).

The code itself is never stored, logged or put in a notification row. Only an
HMAC of "phone:purpose:code" is kept, so a copy of the database does not let
anyone log in. Rate limits read the database, not the cache, so a restart or a
second worker cannot reset them.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import OtpChallenge
from apps.core.phone import normalise_phone
from apps.notifications.sms import get_sms_backend

# How long a code stays valid (D59 policy constants).
CODE_VALIDITY = timedelta(minutes=5)
# Wrong tries allowed on one challenge before it is killed.
MAX_ATTEMPTS = 5
# The same phone may not ask for a new code this soon.
MIN_SECONDS_BETWEEN_REQUESTS = 30
# Requests allowed for one phone in any rolling hour.
MAX_REQUESTS_PER_HOUR = 5

CODE_LENGTH = 6

# Reasons returned by verify_code; the view shows one generic message for all
# of them, so a caller cannot tell a wrong code from an expired one.
REASON_OK = "ok"
REASON_NO_CHALLENGE = "no_challenge"
REASON_EXPIRED = "expired"
REASON_TOO_MANY_ATTEMPTS = "too_many_attempts"
REASON_WRONG_CODE = "wrong_code"

# Reasons returned by request_code when no message was sent.
REASON_TOO_SOON = "too_soon"
REASON_TOO_MANY_REQUESTS = "too_many_requests"


@dataclass(frozen=True)
class OtpRequestResult:
    """What happened when a phone asked for a code."""

    phone: str
    sent: bool
    reason: str = ""


@dataclass(frozen=True)
class OtpVerifyResult:
    """The verified phone, or None plus a reason when the code was refused."""

    phone: str | None
    reason: str

    @property
    def ok(self) -> bool:
        # A property lets callers write ``result.ok`` without a method call.
        return self.phone is not None


def generate_code() -> str:
    """Return a random six-digit code, keeping a leading zero.

    ``secrets`` is the cryptographic random source, unlike ``random``.
    """
    # randbelow(1_000_000) gives 0 to 999999; the :06d format pads to six digits.
    return f"{secrets.randbelow(1_000_000):06d}"


def _code_hmac(phone: str, purpose: str, code: str) -> str:
    """HMAC-SHA256 hex digest of the three values, keyed with SECRET_KEY."""
    message = f"{phone}:{purpose}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def lock_phone_for_otp(phone: str) -> None:
    """Take a per-phone advisory lock for the rest of the transaction (D59).

    Two requests for the same phone must queue, or both could pass the resend
    check and send two codes. ``pg_advisory_xact_lock`` is a PostgreSQL lock
    that is not tied to a row and is released automatically when the current
    transaction commits or rolls back, so it must be called inside
    ``transaction.atomic()``. ``hashtext`` turns the phone string into the
    integer key the lock function expects.
    """
    normalised = normalise_phone(phone)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"otp:{normalised}"])


def request_code(
    phone: str,
    *,
    purpose: str = OtpChallenge.Purpose.LOGIN,
    send: Callable[[str], None] | None = None,
) -> OtpRequestResult:
    """Create a challenge and deliver one code, or refuse without sending.

    Known and unknown phones follow the same path, so the caller cannot use
    this to discover who has an account (D59). ``purpose`` keeps codes for
    different flows apart (D71), and ``send`` lets the caller deliver the code
    another way, for example by email; the default sends the SMS.
    """
    normalised = normalise_phone(phone)
    now = timezone.now()

    with transaction.atomic():
        # Hold the phone lock before the resend check, so two requests at the
        # same moment cannot both read "no recent challenge" and both send.
        lock_phone_for_otp(normalised)
        latest = (
            OtpChallenge.objects.filter(phone=normalised, purpose=purpose)
            .order_by("-created_at", "-pk")
            .first()
        )
        if latest is not None:
            seconds_since = (now - latest.created_at).total_seconds()
            if seconds_since < MIN_SECONDS_BETWEEN_REQUESTS:
                return OtpRequestResult(
                    phone=normalised,
                    sent=False,
                    reason=REASON_TOO_SOON,
                )

        one_hour_ago = now - timedelta(hours=1)
        recent_count = OtpChallenge.objects.filter(
            phone=normalised,
            purpose=purpose,
            created_at__gte=one_hour_ago,
        ).count()
        if recent_count >= MAX_REQUESTS_PER_HOUR:
            return OtpRequestResult(
                phone=normalised,
                sent=False,
                reason=REASON_TOO_MANY_REQUESTS,
            )

        # A new request replaces the old one: every unused challenge for this
        # phone is marked consumed, so an older code can never be replayed.
        OtpChallenge.objects.filter(
            phone=normalised,
            purpose=purpose,
            consumed_at__isnull=True,
        ).update(consumed_at=now)

        code = generate_code()
        OtpChallenge.objects.create(
            phone=normalised,
            purpose=purpose,
            code_hmac=_code_hmac(normalised, purpose, code),
            created_at=now,
            expires_at=now + CODE_VALIDITY,
        )

    # Send after the transaction commits, so a slow SMS provider never holds a
    # database lock open. A custom sender replaces the SMS call completely.
    if send is not None:
        send(code)
    else:
        get_sms_backend().send_otp(normalised, code)
    return OtpRequestResult(phone=normalised, sent=True, reason=REASON_OK)


def verify_code(
    phone: str,
    code: str,
    *,
    purpose: str = OtpChallenge.Purpose.LOGIN,
) -> OtpVerifyResult:
    """Check one code against the newest unused challenge for this phone.

    The challenge row is locked with ``select_for_update()`` so two wrong tries
    at the same moment are both counted, instead of one overwriting the other.
    Only a challenge of the same ``purpose`` is ever checked (D71).
    """
    normalised = normalise_phone(phone)
    now = timezone.now()

    with transaction.atomic():
        # The phone lock serialises verification with a new request for the
        # same phone, so a code cannot be checked against a challenge that a
        # concurrent request has just replaced.
        lock_phone_for_otp(normalised)
        challenge = (
            OtpChallenge.objects.select_for_update()
            .filter(
                phone=normalised,
                purpose=purpose,
                consumed_at__isnull=True,
            )
            .order_by("-created_at", "-pk")
            .first()
        )
        if challenge is None:
            return OtpVerifyResult(phone=None, reason=REASON_NO_CHALLENGE)

        if challenge.expires_at <= now:
            return OtpVerifyResult(phone=None, reason=REASON_EXPIRED)

        if challenge.attempts >= MAX_ATTEMPTS:
            return OtpVerifyResult(phone=None, reason=REASON_TOO_MANY_ATTEMPTS)

        expected = _code_hmac(normalised, purpose, code)
        # compare_digest compares in constant time, so the response time does
        # not leak how many leading characters of the stored HMAC matched.
        if not hmac.compare_digest(expected, challenge.code_hmac):
            challenge.attempts += 1
            fields = ["attempts"]
            if challenge.attempts >= MAX_ATTEMPTS:
                # The last allowed try kills the challenge outright.
                challenge.consumed_at = now
                fields.append("consumed_at")
            challenge.save(update_fields=fields)
            return OtpVerifyResult(phone=None, reason=REASON_WRONG_CODE)

        # One correct code closes every other unused challenge for this phone,
        # not only the row just checked: an older code that arrived before a
        # newer request must never be usable afterwards. One queryset update
        # does it without a loop.
        OtpChallenge.objects.filter(
            phone=normalised,
            purpose=purpose,
            consumed_at__isnull=True,
        ).update(consumed_at=now)
        return OtpVerifyResult(phone=normalised, reason=REASON_OK)


def delete_old_challenges(days: int = 1) -> int:
    """Delete challenges older than ``days`` and return how many rows went."""
    cutoff = timezone.now() - timedelta(days=days)
    deleted, _details = OtpChallenge.objects.filter(created_at__lt=cutoff).delete()
    return deleted
