"""Tests for the delivery-fee GST rate on ShopSettings (D80)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError

from apps.core.audit import (
    AUDITED_SHOP_SETTINGS_FIELDS,
    SHOP_SETTINGS_UPDATE_ACTION,
    record_shop_settings_change,
    snapshot_shop_settings_from_db,
)
from apps.core.models import SHOP_SETTINGS_CACHE_KEY, ShopSettings

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_the_settings_cache():
    """Empty the per-process cache the settings row is read through."""
    cache.delete(SHOP_SETTINGS_CACHE_KEY)
    yield
    cache.delete(SHOP_SETTINGS_CACHE_KEY)


def test_the_delivery_fee_gst_rate_defaults_to_18() -> None:
    row = ShopSettings.load()

    assert row.delivery_fee_gst_percent == Decimal("18.00")


def test_a_rate_over_100_is_refused() -> None:
    row = ShopSettings.load()
    row.delivery_fee_gst_percent = Decimal("100.01")

    with pytest.raises(ValidationError) as error:
        row.full_clean()

    # A database check constraint is reported under the "__all__" key.
    assert "__all__" in error.value.error_dict


def test_a_negative_rate_is_refused() -> None:
    row = ShopSettings.load()
    row.delivery_fee_gst_percent = Decimal("-0.01")

    with pytest.raises(ValidationError) as error:
        row.full_clean()

    assert "__all__" in error.value.error_dict


def test_the_rate_is_in_the_audited_fields() -> None:
    assert "delivery_fee_gst_percent" in AUDITED_SHOP_SETTINGS_FIELDS


def test_the_help_text_is_honest_about_the_unknown_rate() -> None:
    field = ShopSettings._meta.get_field("delivery_fee_gst_percent")

    assert field.help_text == "UNCONFIRMED: ask your CA"


def test_a_rate_change_writes_an_audit_row() -> None:
    row = ShopSettings.load()
    # The old values are read from the database BEFORE the save.
    old = snapshot_shop_settings_from_db()
    row.delivery_fee_gst_percent = Decimal("12.00")
    row.save(update_fields=["delivery_fee_gst_percent", "updated_at"])

    entry = record_shop_settings_change(actor=None, settings_row=row, old=old)

    assert entry is not None
    assert entry.action == SHOP_SETTINGS_UPDATE_ACTION
    assert entry.changes == {"delivery_fee_gst_percent": ["18.00", "12.00"]}
