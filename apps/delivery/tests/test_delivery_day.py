"""Tests for the delivery-day rule (Slice 33, D62)."""

from __future__ import annotations

from datetime import date, datetime, time
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache

from apps.core.models import SHOP_SETTINGS_CACHE_KEY, ShopSettings
from apps.delivery.services import get_delivery_promise

pytestmark = pytest.mark.django_db

# D4 fixes the shop time zone; every fixed moment below is built in it.
IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture(autouse=True)
def _clear_the_settings_cache():
    cache.delete(SHOP_SETTINGS_CACHE_KEY)
    yield
    cache.delete(SHOP_SETTINGS_CACHE_KEY)


def _at(hour: int, minute: int, second: int = 0, microsecond: int = 0) -> datetime:
    """A fixed aware moment on 6 November 2026 in the shop time zone."""
    return datetime(2026, 11, 6, hour, minute, second, microsecond, tzinfo=IST)


# ---------------------------------------------------------------------------
# The cutoff comparison
# ---------------------------------------------------------------------------
def test_before_the_cutoff_is_same_day() -> None:
    promise = get_delivery_promise(_at(16, 59))

    assert promise.day == date(2026, 11, 6)
    assert promise.is_same_day is True


def test_one_microsecond_before_the_cutoff_is_same_day() -> None:
    promise = get_delivery_promise(_at(16, 59, 59, 999999))

    assert promise.day == date(2026, 11, 6)
    assert promise.is_same_day is True


def test_exactly_at_the_cutoff_is_next_day() -> None:
    promise = get_delivery_promise(_at(17, 0))

    assert promise.day == date(2026, 11, 7)
    assert promise.is_same_day is False


def test_after_the_cutoff_is_next_day() -> None:
    promise = get_delivery_promise(_at(17, 0, 0, 1))

    assert promise.day == date(2026, 11, 7)
    assert promise.is_same_day is False


def test_just_after_midnight_is_same_day() -> None:
    promise = get_delivery_promise(_at(0, 0, 1))

    assert promise.day == date(2026, 11, 6)
    assert promise.is_same_day is True


def test_just_before_midnight_is_next_day_and_the_date_rolls_over() -> None:
    promise = get_delivery_promise(_at(23, 59, 59))

    assert promise.day == date(2026, 11, 7)
    assert promise.is_same_day is False


def test_the_rule_uses_the_shop_time_zone_not_utc() -> None:
    utc = ZoneInfo("UTC")
    # 11:29 UTC is 16:59 in India; 11:30 UTC is exactly the 17:00 cutoff.
    before = datetime(2026, 11, 6, 11, 29, tzinfo=utc)
    at_cutoff = datetime(2026, 11, 6, 11, 30, tzinfo=utc)

    assert get_delivery_promise(before).is_same_day is True
    assert get_delivery_promise(at_cutoff).is_same_day is False


def test_a_naive_datetime_is_refused() -> None:
    naive = datetime(2026, 11, 6, 16, 0)

    with pytest.raises(ValueError):
        get_delivery_promise(naive)


# ---------------------------------------------------------------------------
# The cutoff is shop data
# ---------------------------------------------------------------------------
def test_the_cutoff_comes_from_shop_settings() -> None:
    row = ShopSettings.load()
    row.same_day_cutoff = time(10, 0)
    row.save(update_fields=["same_day_cutoff", "updated_at"])

    promise = get_delivery_promise(_at(9, 59))

    assert promise.is_same_day is True
    assert promise.cutoff == time(10, 0)


def test_a_settings_change_is_seen_on_the_next_call() -> None:
    # Prime the cache with the default 17:00 cutoff.
    assert get_delivery_promise(_at(16, 30)).is_same_day is True
    row = ShopSettings.load()
    row.same_day_cutoff = time(16, 0)
    row.save(update_fields=["same_day_cutoff", "updated_at"])

    assert get_delivery_promise(_at(16, 30)).is_same_day is False


# ---------------------------------------------------------------------------
# The labels and the carried cutoff
# ---------------------------------------------------------------------------
def test_the_same_day_label_is_today() -> None:
    assert get_delivery_promise(_at(12, 0)).label == "Today"


def test_the_next_day_label_is_tomorrow_morning() -> None:
    assert get_delivery_promise(_at(18, 0)).label == "Tomorrow morning"


def test_the_promise_carries_the_cutoff_time() -> None:
    assert get_delivery_promise(_at(12, 0)).cutoff == time(17, 0)


def test_now_defaults_to_the_current_time() -> None:
    fixed_now = _at(16, 0)

    # Patch the module's clock, so the no-argument call uses this fixed moment.
    with patch("apps.delivery.services.timezone.now", return_value=fixed_now):
        promise = get_delivery_promise()

    assert promise.day == date(2026, 11, 6)
    assert promise.is_same_day is True


def test_month_end_rolls_to_the_first_of_next_month() -> None:
    end_of_month = datetime(2026, 11, 30, 18, 0, tzinfo=IST)

    promise = get_delivery_promise(end_of_month)

    assert promise.day == date(2026, 12, 1)
    assert promise.is_same_day is False
