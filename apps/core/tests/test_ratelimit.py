"""The per-IP counter: the address it reads and the cache keys it builds (D89).

The cache is locmem in tests and survives a database rollback, so every test
clears it first.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.http import HttpRequest
from django.test import RequestFactory

from apps.core.ratelimit import (
    SCOPE_LOGIN,
    SCOPE_OTP,
    client_ip,
    is_limited,
    record,
)

pytestmark = pytest.mark.django_db

VISITOR = "203.0.113.7"
OTHER_VISITOR = "203.0.113.8"
PROXY_HEADER = "HTTP_CF_CONNECTING_IP"


@pytest.fixture(autouse=True)
def _empty_cache():
    """A counter from another test must never change this one's answer."""
    cache.clear()
    yield
    cache.clear()


def _request(**meta: str) -> HttpRequest:
    """A plain GET request with the META values a test needs."""
    return RequestFactory().get("/", **meta)


def _hour(moment: datetime):
    """A stand-in for ``django.utils.timezone`` with a frozen clock."""
    return SimpleNamespace(now=lambda: moment)


# ---------------------------------------------------------------------------
# The address
# ---------------------------------------------------------------------------
def test_client_ip_uses_remote_addr_by_default(settings) -> None:
    settings.CLIENT_IP_HEADER = ""

    request = _request(REMOTE_ADDR=VISITOR, **{PROXY_HEADER: "198.51.100.1"})

    assert client_ip(request) == VISITOR


def test_client_ip_uses_configured_header(settings) -> None:
    settings.CLIENT_IP_HEADER = PROXY_HEADER

    request = _request(REMOTE_ADDR=VISITOR, **{PROXY_HEADER: "198.51.100.1"})

    assert client_ip(request) == "198.51.100.1"


def test_client_ip_takes_first_value_of_list(settings) -> None:
    settings.CLIENT_IP_HEADER = PROXY_HEADER

    request = _request(
        REMOTE_ADDR=VISITOR,
        **{PROXY_HEADER: " 198.51.100.1 , 10.0.0.1 "},
    )

    assert client_ip(request) == "198.51.100.1"


def test_client_ip_ignores_invalid_header_value(settings) -> None:
    settings.CLIENT_IP_HEADER = PROXY_HEADER

    request = _request(REMOTE_ADDR=VISITOR, **{PROXY_HEADER: "not-an-address"})

    assert client_ip(request) == VISITOR


def test_client_ip_falls_back_when_header_missing(settings) -> None:
    settings.CLIENT_IP_HEADER = PROXY_HEADER

    request = _request(REMOTE_ADDR=VISITOR)

    assert client_ip(request) == VISITOR


# ---------------------------------------------------------------------------
# The counter
# ---------------------------------------------------------------------------
def test_limit_allows_up_to_limit_then_blocks() -> None:
    request = _request(REMOTE_ADDR=VISITOR)

    record(request, SCOPE_OTP)
    record(request, SCOPE_OTP)
    assert is_limited(request, SCOPE_OTP, 3) is False

    record(request, SCOPE_OTP)

    # The limit is reached: twenty of twenty is already too many.
    assert is_limited(request, SCOPE_OTP, 3) is True
    assert is_limited(request, SCOPE_OTP, 4) is False


def test_limit_is_per_ip() -> None:
    first = _request(REMOTE_ADDR=VISITOR)
    second = _request(REMOTE_ADDR=OTHER_VISITOR)

    record(first, SCOPE_OTP)

    assert is_limited(first, SCOPE_OTP, 1) is True
    assert is_limited(second, SCOPE_OTP, 1) is False


def test_limit_is_per_scope() -> None:
    request = _request(REMOTE_ADDR=VISITOR)

    record(request, SCOPE_OTP)

    assert is_limited(request, SCOPE_OTP, 1) is True
    assert is_limited(request, SCOPE_LOGIN, 1) is False


def test_limit_resets_in_next_hour_bucket() -> None:
    request = _request(REMOTE_ADDR=VISITOR)
    first_hour = datetime(2026, 11, 14, 9, 30, tzinfo=UTC)

    with patch("apps.core.ratelimit.timezone", _hour(first_hour)):
        record(request, SCOPE_OTP)
        assert is_limited(request, SCOPE_OTP, 1) is True

    with patch("apps.core.ratelimit.timezone", _hour(first_hour + timedelta(hours=1))):
        assert is_limited(request, SCOPE_OTP, 1) is False


def test_cache_error_fails_open() -> None:
    request = _request(REMOTE_ADDR=VISITOR)

    with patch("apps.core.ratelimit.cache") as broken:
        broken.get.side_effect = RuntimeError("cache down")
        broken.add.side_effect = RuntimeError("cache down")
        broken.incr.side_effect = RuntimeError("cache down")

        assert is_limited(request, SCOPE_OTP, 1) is False
        # Counting must not raise either: a broken cache never blocks a login.
        record(request, SCOPE_OTP)
