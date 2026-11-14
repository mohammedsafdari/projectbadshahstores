"""Per-IP rate limits for the login-side forms (D89).

One counter lives in the Django cache, under one key per scope, IP address and
hour. No IP address and no count is ever written to the database: a limit is a
momentary guard, not a record of a visitor.

A cache problem always fails open: a broken cache must never stop a customer
from logging in.
"""

from __future__ import annotations

import ipaddress
import logging

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest
from django.utils import timezone

logger = logging.getLogger(__name__)

# The limits, per IP address, per hour (D89).
OTP_REQUESTS_PER_HOUR = 20
SIGNUPS_PER_HOUR = 20
PASSWORD_FAILURES_PER_HOUR = 30

# The three scopes, so one form's counter can never block another.
SCOPE_OTP = "otp"
SCOPE_SIGNUP = "signup"
# The password login form. Its key prefix matches the one the per-account
# throttle in ``apps.accounts.passwords`` already uses.
SCOPE_LOGIN = "password-login"

# One hour: the key expires by itself at the end of its own window.
WINDOW_SECONDS = 3600

# The hour bucket format: 2026-11-14 09:xx becomes "2026111409", because
# strftime with "%Y%m%d%H" writes the year, month, day and hour.
HOUR_FORMAT = "%Y%m%d%H"


def client_ip(request: HttpRequest) -> str:
    """The visitor's address, through the configured proxy header when set.

    A public site behind Cloudflare sees the proxy's address in REMOTE_ADDR, so
    ``CLIENT_IP_HEADER`` names the header that carries the real one. Only the
    first value of a comma-separated header is used, and only when it parses as
    an IP address, so a typed header can never reach the cache key (D89).
    """
    header = str(getattr(settings, "CLIENT_IP_HEADER", "") or "").strip()
    if header:
        # META keys are upper-cased and use underscores: HTTP_CF_CONNECTING_IP.
        raw = request.META.get(header, "")
        # The first part of "1.2.3.4, 5.6.7.8" is the visitor's own address.
        first = str(raw).split(",")[0].strip()
        if first:
            try:
                ipaddress.ip_address(first)
            except ValueError:
                # A header the visitor typed is not an address to trust.
                pass
            else:
                return first
    return str(request.META.get("REMOTE_ADDR", "") or "")


def _cache_key(request: HttpRequest, scope: str) -> str:
    """One key per scope, address and hour: ``rl:<scope>:<ip>:<hour>``."""
    bucket = timezone.now().strftime(HOUR_FORMAT)
    return f"rl:{scope}:{client_ip(request)}:{bucket}"


def is_limited(request: HttpRequest, scope: str, limit: int) -> bool:
    """True when this address has already used ``limit`` tries in this hour."""
    try:
        count = cache.get(_cache_key(request, scope), 0)
    except Exception:
        # One warning with the scope only: never the address.
        logger.warning("Rate limit cache failed: scope=%s", scope)
        return False
    return int(count) >= limit


def record(request: HttpRequest, scope: str) -> None:
    """Count one try for this address, and never raise (D89).

    ``cache.add`` creates the counter only when it is missing, so two requests
    at the same moment both call ``incr`` on an existing key instead of both
    writing 1.
    """
    try:
        key = _cache_key(request, scope)
        cache.add(key, 0, timeout=WINDOW_SECONDS)
        cache.incr(key)
    except Exception:
        logger.warning("Rate limit cache failed: scope=%s", scope)
