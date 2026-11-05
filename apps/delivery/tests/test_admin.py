"""Tests for the delivery-zone admin (Slice 33, D62)."""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.delivery.admin import (
    DELIVERY_PIN_CODES_UPDATE_ACTION,
    DELIVERY_ZONE_UPDATE_ACTION,
)
from apps.delivery.models import DeliveryZone, PinCodeZone
from apps.delivery.tests.factories import DeliveryZoneFactory, PinCodeZoneFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _superuser(phone: str = "+919876543210") -> User:
    return User.objects.create_superuser(phone=phone, password="ok")


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _zone_data(**overrides: Any) -> dict[str, Any]:
    """The zone form plus the inline management form, with no pin codes."""
    data: dict[str, Any] = {
        "name": "Indore",
        "is_active": "on",
        "pin_codes-TOTAL_FORMS": "0",
        "pin_codes-INITIAL_FORMS": "0",
        "pin_codes-MIN_NUM_FORMS": "0",
        "pin_codes-MAX_NUM_FORMS": "1000",
    }
    data.update(overrides)
    return data


def _change_url(zone: DeliveryZone) -> str:
    return reverse("admin:delivery_deliveryzone_change", args=[zone.pk])


# ---------------------------------------------------------------------------
# Pages and permissions
# ---------------------------------------------------------------------------
def test_zone_changelist_loads_for_a_superuser() -> None:
    client = _client(_superuser())

    response = client.get(reverse("admin:delivery_deliveryzone_changelist"))

    assert response.status_code == 200


def test_zone_add_page_loads() -> None:
    client = _client(_superuser())

    response = client.get(reverse("admin:delivery_deliveryzone_add"))

    assert response.status_code == 200


def test_a_user_without_permission_is_refused() -> None:
    staff = User.objects.create_user(phone="+919876543220", password="ok", is_staff=True)
    client = _client(staff)

    response = client.get(reverse("admin:delivery_deliveryzone_changelist"))

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Creating a zone and its pin codes
# ---------------------------------------------------------------------------
def test_a_zone_can_be_created_with_pin_codes_inline() -> None:
    client = _client(_superuser())
    data = _zone_data(
        **{
            "pin_codes-TOTAL_FORMS": "1",
            "pin_codes-0-pincode": "452012",
        }
    )

    response = client.post(reverse("admin:delivery_deliveryzone_add"), data)

    assert response.status_code == 302
    zone = DeliveryZone.objects.get(name="Indore")
    assert PinCodeZone.objects.get(zone=zone).pincode == "452012"


def test_the_inline_rejects_a_bad_pin_code() -> None:
    client = _client(_superuser())
    data = _zone_data(
        **{
            "name": "Ujjain",
            "pin_codes-TOTAL_FORMS": "1",
            "pin_codes-0-pincode": "052012",
        }
    )

    response = client.post(reverse("admin:delivery_deliveryzone_add"), data)
    html = response.content.decode()

    assert response.status_code == 200
    assert "first digit cannot be 0" in html
    assert not DeliveryZone.objects.filter(name="Ujjain").exists()


def test_a_pin_code_in_another_zone_is_refused_with_a_message() -> None:
    first = DeliveryZoneFactory(name="Indore")
    PinCodeZoneFactory(zone=first, pincode="452012")
    client = _client(_superuser())
    data = _zone_data(
        **{
            "name": "Ujjain",
            "pin_codes-TOTAL_FORMS": "1",
            "pin_codes-0-pincode": "452012",
        }
    )

    response = client.post(reverse("admin:delivery_deliveryzone_add"), data)
    html = response.content.decode()

    assert response.status_code == 200
    assert "This pin code is already in another zone." in html
    assert not DeliveryZone.objects.filter(name="Ujjain").exists()


# ---------------------------------------------------------------------------
# Audit rows
# ---------------------------------------------------------------------------
def test_zone_rename_writes_an_audit_row() -> None:
    zone = DeliveryZoneFactory(name="Indore")
    client = _client(_superuser())

    response = client.post(_change_url(zone), _zone_data(name="Indore City"))

    assert response.status_code == 302
    audit = AuditLog.objects.get(action=DELIVERY_ZONE_UPDATE_ACTION)
    assert audit.changes == {"name": ["Indore", "Indore City"]}


def test_zone_deactivation_writes_an_audit_row() -> None:
    zone = DeliveryZoneFactory(name="Indore")
    client = _client(_superuser())
    data = _zone_data()
    # An unchecked BooleanField is simply absent from the POST data.
    data.pop("is_active")

    response = client.post(_change_url(zone), data)

    assert response.status_code == 302
    audit = AuditLog.objects.get(action=DELIVERY_ZONE_UPDATE_ACTION)
    assert audit.changes == {"is_active": [True, False]}


def test_adding_a_pin_code_writes_an_audit_row() -> None:
    zone = DeliveryZoneFactory(name="Indore")
    client = _client(_superuser())
    data = _zone_data(
        **{
            "pin_codes-TOTAL_FORMS": "1",
            "pin_codes-0-pincode": "452012",
        }
    )

    response = client.post(_change_url(zone), data)

    assert response.status_code == 302
    audit = AuditLog.objects.get(action=DELIVERY_PIN_CODES_UPDATE_ACTION)
    assert audit.changes == {"pin_codes": ["", "452012"]}


def test_removing_a_pin_code_writes_an_audit_row() -> None:
    zone = DeliveryZoneFactory(name="Indore")
    pin = PinCodeZoneFactory(zone=zone, pincode="452012")
    client = _client(_superuser())
    data = _zone_data(
        **{
            "pin_codes-TOTAL_FORMS": "1",
            "pin_codes-INITIAL_FORMS": "1",
            "pin_codes-0-id": str(pin.pk),
            "pin_codes-0-pincode": "452012",
            "pin_codes-0-DELETE": "on",
        }
    )

    response = client.post(_change_url(zone), data)

    assert response.status_code == 302
    assert not PinCodeZone.objects.filter(zone=zone).exists()
    audit = AuditLog.objects.get(action=DELIVERY_PIN_CODES_UPDATE_ACTION)
    assert audit.changes == {"pin_codes": ["452012", ""]}


def test_saving_without_changes_writes_no_audit_row() -> None:
    zone = DeliveryZoneFactory(name="Indore")
    pin = PinCodeZoneFactory(zone=zone, pincode="452012")
    client = _client(_superuser())
    data = _zone_data(
        **{
            "pin_codes-TOTAL_FORMS": "1",
            "pin_codes-INITIAL_FORMS": "1",
            "pin_codes-0-id": str(pin.pk),
            "pin_codes-0-pincode": "452012",
        }
    )

    response = client.post(_change_url(zone), data)

    assert response.status_code == 302
    assert not AuditLog.objects.exists()


def test_audit_rows_record_the_acting_user() -> None:
    zone = DeliveryZoneFactory(name="Indore")
    user = _superuser()
    client = _client(user)

    client.post(_change_url(zone), _zone_data(name="Indore City"))

    audit = AuditLog.objects.get(action=DELIVERY_ZONE_UPDATE_ACTION)
    assert audit.actor_id == user.pk


def test_the_audit_row_lists_the_old_and_new_pin_codes() -> None:
    zone = DeliveryZoneFactory(name="Indore")
    first = PinCodeZoneFactory(zone=zone, pincode="452012")
    second = PinCodeZoneFactory(zone=zone, pincode="452014")
    client = _client(_superuser())
    data = _zone_data(
        **{
            "pin_codes-TOTAL_FORMS": "3",
            "pin_codes-INITIAL_FORMS": "2",
            "pin_codes-0-id": str(first.pk),
            "pin_codes-0-pincode": "452012",
            "pin_codes-1-id": str(second.pk),
            "pin_codes-1-pincode": "452014",
            "pin_codes-2-pincode": "453331",
        }
    )

    client.post(_change_url(zone), data)

    audit = AuditLog.objects.get(action=DELIVERY_PIN_CODES_UPDATE_ACTION)
    assert audit.changes == {
        "pin_codes": ["452012, 452014", "452012, 452014, 453331"],
    }


# ---------------------------------------------------------------------------
# Protected delete
# ---------------------------------------------------------------------------
def test_deleting_a_zone_with_pin_codes_is_refused_with_a_message() -> None:
    zone = DeliveryZoneFactory(name="Indore")
    PinCodeZoneFactory(zone=zone, pincode="452012")
    client = _client(_superuser())

    response = client.post(
        reverse("admin:delivery_deliveryzone_delete", args=[zone.pk]),
        {"post": "yes"},
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "protected related objects" in html
    assert DeliveryZone.objects.filter(pk=zone.pk).exists()
