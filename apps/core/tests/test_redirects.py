"""Tests for the shared safe-redirect helper (D65)."""

from __future__ import annotations

from django.http import HttpRequest
from django.test import RequestFactory

from apps.core.redirects import safe_next_url


def _request() -> HttpRequest:
    return RequestFactory().get("/account/addresses/")


def test_safe_next_url_accepts_a_relative_path() -> None:
    assert safe_next_url(_request(), "/cart/") == "/cart/"


def test_safe_next_url_refuses_another_host() -> None:
    assert safe_next_url(_request(), "https://evil.example/phish") is None


def test_safe_next_url_refuses_a_protocol_relative_url() -> None:
    assert safe_next_url(_request(), "//evil.example/phish") is None


def test_safe_next_url_returns_none_for_an_empty_value() -> None:
    assert safe_next_url(_request(), "") is None
