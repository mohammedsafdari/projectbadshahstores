"""Tests for the public delivery-check view (Slice 33, D62)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse

from apps.core.models import SHOP_SETTINGS_CACHE_KEY
from apps.delivery.tests.factories import DeliveryZoneFactory, PinCodeZoneFactory

pytestmark = pytest.mark.django_db

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture(autouse=True)
def _clear_the_settings_cache():
    cache.delete(SHOP_SETTINGS_CACHE_KEY)
    yield
    cache.delete(SHOP_SETTINGS_CACHE_KEY)


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 6, hour, minute, tzinfo=IST)


def _served_zone() -> None:
    zone = DeliveryZoneFactory(name="Indore")
    PinCodeZoneFactory(zone=zone, pincode="452012")


def _get(client: Client, pincode: str | None = None, *, htmx: bool = False) -> HttpResponse:
    data = {} if pincode is None else {"pincode": pincode}
    headers = {"HTTP_HX_REQUEST": "true"} if htmx else {}
    return client.get(reverse("delivery_check"), data, **headers)


# ---------------------------------------------------------------------------
# The request shape
# ---------------------------------------------------------------------------
def test_the_check_is_get_only() -> None:
    assert Client().post(reverse("delivery_check")).status_code == 405


def test_htmx_check_returns_a_fragment_only() -> None:
    response = _get(Client(), "452099", htmx=True)
    html = response.content.decode()

    assert response.status_code == 200
    assert html.startswith('<div role="alert"')
    assert "<html" not in html


def test_non_htmx_check_returns_a_full_page() -> None:
    html = _get(Client(), "452099").content.decode()

    assert "<html" in html
    assert "Check delivery" in html


def test_delivery_check_url() -> None:
    assert reverse("delivery_check") == "/delivery/check/"


# ---------------------------------------------------------------------------
# The wording
# ---------------------------------------------------------------------------
def test_served_pin_message_names_the_pin_code_and_zone() -> None:
    _served_zone()

    html = _get(Client(), "452012").content.decode()

    assert "We deliver to 452012 (Indore)." in html


def test_unserved_pin_message_says_we_do_not_deliver_yet() -> None:
    html = _get(Client(), "452099").content.decode()

    assert "Sorry, we do not deliver to 452099 yet." in html


def test_invalid_pin_message_asks_for_six_digits() -> None:
    html = _get(Client(), "abc").content.decode()

    assert "Enter a six-digit pin code." in html


def test_missing_pincode_parameter_is_the_invalid_message_not_a_500() -> None:
    response = _get(Client())
    html = response.content.decode()

    assert response.status_code == 200
    assert "Enter a six-digit pin code." in html


def test_after_the_cutoff_the_message_says_tomorrow_morning() -> None:
    _served_zone()

    with patch("apps.delivery.services.timezone.now", return_value=_at(18, 0)):
        html = _get(Client(), "452012").content.decode()

    assert "Orders placed now are delivered tomorrow morning." in html


def test_before_the_cutoff_the_message_says_today() -> None:
    _served_zone()

    with patch("apps.delivery.services.timezone.now", return_value=_at(16, 0)):
        html = _get(Client(), "452012").content.decode()

    assert "Order before 5:00 PM and we deliver today." in html


def test_success_is_a_status_and_errors_are_alerts() -> None:
    _served_zone()
    client = Client()

    assert 'role="status"' in _get(client, "452012").content.decode()
    assert 'role="alert"' in _get(client, "452099").content.decode()
    assert 'role="alert"' in _get(client, "abc").content.decode()


def test_invalid_input_is_not_echoed_back() -> None:
    html = _get(Client(), "<script>alert(1)</script>").content.decode()

    assert "alert(1)" not in html
    assert "Enter a six-digit pin code." in html


def test_the_check_is_not_cacheable() -> None:
    response = _get(Client(), "452099")

    assert "no-store" in response["Cache-Control"]


def test_the_check_needs_no_login() -> None:
    # A brand-new client has no session user at all; the answer is still 200.
    response = _get(Client(), "452099")

    assert response.status_code == 200


def test_the_cutoff_time_is_shown_as_5_00_pm() -> None:
    _served_zone()

    with patch("apps.delivery.services.timezone.now", return_value=_at(16, 0)):
        html = _get(Client(), "452012").content.decode()

    assert "5:00 PM" in html


def test_the_zone_name_is_escaped_in_the_message() -> None:
    zone = DeliveryZoneFactory(name="<b>x</b>")
    PinCodeZoneFactory(zone=zone, pincode="452012")

    html = _get(Client(), "452012").content.decode()

    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "<b>x</b>" not in html
