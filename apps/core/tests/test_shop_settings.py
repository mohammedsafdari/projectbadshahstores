"""Tests for ShopSettings (Slice 29e, ADR D58).

The model tests cover the defaults, the singleton and the check constraints.
The service tests prove the 60-second cache and its clearing on save. The admin
tests post to the real change page, so Django's form, the model save and the
audit helper are exercised together.
"""

from datetime import time
from decimal import Decimal
from typing import Any

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import User
from apps.core.audit import AUDITED_SHOP_SETTINGS_FIELDS, SHOP_SETTINGS_UPDATE_ACTION
from apps.core.models import SHOP_SETTINGS_CACHE_KEY, AuditLog, ShopSettings
from apps.core.services import get_shop_settings

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_the_settings_cache():
    """Empty the cache around every test.

    The test settings use a per-process locmem cache, which is not rolled back
    with the database, so a cached row from one test could leak into the next.
    """
    cache.delete(SHOP_SETTINGS_CACHE_KEY)
    yield
    cache.delete(SHOP_SETTINGS_CACHE_KEY)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _superuser(phone: str = "+919876543210") -> User:
    return User.objects.create_superuser(phone=phone, password="ok")


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _change_url(row: ShopSettings) -> str:
    return reverse("admin:core_shopsettings_change", args=[row.pk])


def _change_data(row: ShopSettings, **overrides: Any) -> dict[str, Any]:
    """Post the row's current values, so only the overrides are a change.

    Every field must be present: the bound form treats a missing field as
    empty, and a required field such as the cutoff would then fail validation.
    """
    data: dict[str, Any] = {
        "shop_name": row.shop_name,
        "address": row.address,
        "phone": row.phone,
        "email": row.email,
        "gstin": row.gstin,
        "fssai_number": row.fssai_number,
        "fssai_expiry": row.fssai_expiry.isoformat() if row.fssai_expiry else "",
        "grievance_officer_name": row.grievance_officer_name,
        "grievance_officer_phone": row.grievance_officer_phone,
        "grievance_officer_email": row.grievance_officer_email,
        "minimum_order_value": str(row.minimum_order_value),
        "delivery_fee": str(row.delivery_fee),
        "free_delivery_threshold": str(row.free_delivery_threshold),
        "cod_cap": str(row.cod_cap),
        "same_day_cutoff": row.same_day_cutoff.strftime("%H:%M"),
        "reservation_minutes": str(row.reservation_minutes),
        "online_stock_buffer": str(row.online_stock_buffer),
        "order_number_prefix": row.order_number_prefix,
        "invoice_number_prefix": row.invoice_number_prefix,
        "is_published": "on" if row.is_published else "",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# The row, its defaults and its constraints
# ---------------------------------------------------------------------------
def test_load_creates_the_row_with_the_documented_defaults() -> None:
    row = ShopSettings.load()

    assert row.pk == 1
    assert ShopSettings.objects.count() == 1
    assert row.shop_name == ""
    assert row.address == ""
    assert row.phone == ""
    assert row.email == ""
    assert row.gstin == ""
    assert row.fssai_number == ""
    assert row.fssai_expiry is None
    assert row.grievance_officer_name == ""
    assert row.grievance_officer_phone == ""
    assert row.grievance_officer_email == ""
    assert row.minimum_order_value == Decimal("200.00")
    assert row.delivery_fee == Decimal("30.00")
    assert row.free_delivery_threshold == Decimal("500.00")
    assert row.cod_cap == Decimal("2000.00")
    assert row.same_day_cutoff == time(17, 0)
    assert row.reservation_minutes == 15
    assert row.online_stock_buffer == 0
    assert row.order_number_prefix == ""
    assert row.invoice_number_prefix == ""
    assert row.is_published is False


def test_load_twice_returns_the_same_single_row() -> None:
    first = ShopSettings.load()
    second = ShopSettings.load()

    assert first.pk == second.pk == 1
    assert ShopSettings.objects.count() == 1


def test_a_second_row_with_another_pk_is_refused() -> None:
    ShopSettings.load()

    with pytest.raises(IntegrityError), transaction.atomic():
        ShopSettings.objects.create(pk=2)

    assert ShopSettings.objects.count() == 1


@pytest.mark.parametrize(
    "field",
    ["minimum_order_value", "delivery_fee", "free_delivery_threshold", "cod_cap"],
)
def test_negative_money_fields_are_refused(field: str) -> None:
    row = ShopSettings.load()
    setattr(row, field, Decimal("-1.00"))

    with pytest.raises(ValidationError):
        row.full_clean()


def test_negative_online_stock_buffer_is_refused() -> None:
    row = ShopSettings.load()
    row.online_stock_buffer = -1

    with pytest.raises(ValidationError):
        row.full_clean()


def test_zero_reservation_minutes_is_refused() -> None:
    row = ShopSettings.load()
    row.reservation_minutes = 0

    with pytest.raises(ValidationError):
        row.full_clean()


def test_blank_legal_numbers_and_prefixes_pass_full_clean() -> None:
    row = ShopSettings.load()

    row.full_clean()

    assert row.gstin == ""
    assert row.fssai_number == ""
    assert row.order_number_prefix == ""
    assert row.invoice_number_prefix == ""


# ---------------------------------------------------------------------------
# The cached read
# ---------------------------------------------------------------------------
def test_get_shop_settings_second_call_uses_the_cache() -> None:
    first = get_shop_settings()

    # The context manager counts the SQL that runs inside its block. A cache
    # hit must run no query at all.
    with CaptureQueriesContext(connection) as queries:
        second = get_shop_settings()

    assert second.pk == first.pk
    assert len(queries.captured_queries) == 0


def test_save_clears_the_cache() -> None:
    get_shop_settings()
    row = ShopSettings.load()
    row.shop_name = "Badshah Store"
    row.save(update_fields=["shop_name", "updated_at"])

    assert cache.get(SHOP_SETTINGS_CACHE_KEY) is None

    with CaptureQueriesContext(connection) as queries:
        fresh = get_shop_settings()

    assert fresh.shop_name == "Badshah Store"
    # The cleared key means the next read runs one SELECT for the one row.
    assert len(queries.captured_queries) == 1


# ---------------------------------------------------------------------------
# The admin
# ---------------------------------------------------------------------------
def test_admin_add_is_not_allowed() -> None:
    client = _client(_superuser())

    response = client.get(reverse("admin:core_shopsettings_add"))

    assert response.status_code == 403


def test_admin_delete_is_not_allowed() -> None:
    row = ShopSettings.load()
    client = _client(_superuser())

    response = client.get(reverse("admin:core_shopsettings_delete", args=[row.pk]))

    assert response.status_code == 403


def test_admin_change_writes_one_audit_row_with_old_and_new_values() -> None:
    row = ShopSettings.load()
    user = _superuser()
    client = _client(user)

    response = client.post(_change_url(row), _change_data(row, shop_name="Badshah Store"))

    assert response.status_code == 302
    audit = AuditLog.objects.get()
    assert audit.action == SHOP_SETTINGS_UPDATE_ACTION
    assert audit.actor_id == user.pk
    assert audit.changes == {"shop_name": ["", "Badshah Store"]}
    assert set(audit.changes) <= set(AUDITED_SHOP_SETTINGS_FIELDS)


def test_admin_save_without_a_change_writes_no_audit_row() -> None:
    row = ShopSettings.load()
    client = _client(_superuser())

    response = client.post(_change_url(row), _change_data(row))

    assert response.status_code == 302
    assert not AuditLog.objects.exists()


def test_audit_row_target_is_core_shopsettings_1() -> None:
    row = ShopSettings.load()
    client = _client(_superuser())

    client.post(_change_url(row), _change_data(row, delivery_fee="40.00"))

    audit = AuditLog.objects.get()
    assert audit.target_type == "core.shopsettings"
    assert audit.target_id == "1"


def test_admin_save_moves_updated_at() -> None:
    row = ShopSettings.load()
    row.refresh_from_db()  # read the timestamp the database really stores
    before = row.updated_at
    client = _client(_superuser())

    client.post(_change_url(row), _change_data(row, cod_cap="2500.00"))

    row.refresh_from_db()
    assert row.cod_cap == Decimal("2500.00")
    assert row.updated_at > before


def test_a_staff_user_without_change_permission_gets_403() -> None:
    row = ShopSettings.load()
    staff = User.objects.create_user(phone="+919876543220", password="ok", is_staff=True)
    client = _client(staff)

    response = client.get(_change_url(row))

    assert response.status_code == 403
