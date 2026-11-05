"""Tests for the combined delivery check (Slice 33, D62)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache

from apps.core.models import SHOP_SETTINGS_CACHE_KEY
from apps.delivery.services import check_delivery
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
    """One active Indore zone with the pin code the tests ask about."""
    zone = DeliveryZoneFactory(name="Indore")
    PinCodeZoneFactory(zone=zone, pincode="452012")


def test_a_served_pin_gives_a_promise() -> None:
    _served_zone()

    check = check_delivery("452012", _at(12, 0))

    assert check.valid is True
    assert check.served is True
    assert check.promise is not None


def test_an_unserved_pin_gives_no_promise() -> None:
    check = check_delivery("452099", _at(12, 0))

    assert check.valid is True
    assert check.served is False
    assert check.promise is None


def test_an_invalid_pin_is_reported_as_invalid_not_unserved() -> None:
    check = check_delivery("45201", _at(12, 0))

    assert check.valid is False
    assert check.served is False
    assert check.pincode is None


def test_the_result_carries_the_zone_name() -> None:
    _served_zone()

    check = check_delivery("452012", _at(12, 0))

    assert check.zone_name == "Indore"


def test_a_pin_with_spaces_is_normalised() -> None:
    _served_zone()

    check = check_delivery(" 452 012 ", _at(12, 0))

    assert check.pincode == "452012"
    assert check.served is True


def test_the_promise_uses_the_supplied_time() -> None:
    _served_zone()

    check = check_delivery("452012", _at(18, 0))

    assert check.promise is not None
    assert check.promise.is_same_day is False
    assert check.promise.day == date(2026, 11, 7)


def test_empty_input_is_invalid() -> None:
    assert check_delivery("", _at(12, 0)).valid is False
    assert check_delivery(None, _at(12, 0)).valid is False
