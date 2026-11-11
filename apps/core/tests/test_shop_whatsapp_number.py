"""Tests for the shop's WhatsApp number setting (Slice 38d, D70)."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.audit import AUDITED_SHOP_SETTINGS_FIELDS, SHOP_SETTINGS_UPDATE_ACTION
from apps.core.models import SHOP_SETTINGS_CACHE_KEY, AuditLog, ShopSettings

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_the_settings_cache():
    """Empty the cache around every test, as the settings tests do."""
    cache.delete(SHOP_SETTINGS_CACHE_KEY)
    yield
    cache.delete(SHOP_SETTINGS_CACHE_KEY)


def _superuser(phone: str = "+919876543210") -> User:
    return User.objects.create_superuser(phone=phone, password="ok")


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _change_url(row: ShopSettings) -> str:
    return reverse("admin:core_shopsettings_change", args=[row.pk])


def _change_data(row: ShopSettings, **overrides: Any) -> dict[str, Any]:
    """Post the row's current values, so only the overrides are a change."""
    data: dict[str, Any] = {
        "shop_name": row.shop_name,
        "address": row.address,
        "phone": row.phone,
        "whatsapp_number": row.whatsapp_number,
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
        "refund_fee_percent": str(row.refund_fee_percent),
        "same_day_cutoff": row.same_day_cutoff.strftime("%H:%M"),
        "reservation_minutes": str(row.reservation_minutes),
        "online_stock_buffer": str(row.online_stock_buffer),
        "order_number_prefix": row.order_number_prefix,
        "invoice_number_prefix": row.invoice_number_prefix,
        "is_published": "on" if row.is_published else "",
    }
    data.update(overrides)
    return data


def test_whatsapp_number_defaults_to_blank() -> None:
    assert ShopSettings.load().whatsapp_number == ""


def test_whatsapp_number_is_an_audited_field() -> None:
    assert "whatsapp_number" in AUDITED_SHOP_SETTINGS_FIELDS


def test_admin_change_of_the_whatsapp_number_writes_one_audit_row() -> None:
    row = ShopSettings.load()
    user = _superuser()
    client = _client(user)

    response = client.post(
        _change_url(row),
        _change_data(row, whatsapp_number="+919876543210"),
    )

    assert response.status_code == 302
    audit = AuditLog.objects.get()
    assert audit.action == SHOP_SETTINGS_UPDATE_ACTION
    assert audit.changes == {"whatsapp_number": ["", "+919876543210"]}


def test_admin_save_without_changing_the_whatsapp_number_writes_no_audit_row() -> None:
    row = ShopSettings.load()
    client = _client(_superuser())

    response = client.post(_change_url(row), _change_data(row))

    assert response.status_code == 302
    assert not AuditLog.objects.exists()
