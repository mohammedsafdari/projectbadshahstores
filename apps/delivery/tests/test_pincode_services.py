"""Tests for the pin-code serviceability services (Slice 33, D62)."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import Address
from apps.delivery.services import (
    InvalidPincodeError,
    address_is_served,
    get_zone_for_pincode,
    is_pincode_served,
    normalise_pincode,
)
from apps.delivery.tests.factories import DeliveryZoneFactory, PinCodeZoneFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Normalising the typed pin code
# ---------------------------------------------------------------------------
def test_normalise_strips_spaces() -> None:
    assert normalise_pincode(" 452 012 ") == "452012"


def test_normalise_refuses_too_short() -> None:
    with pytest.raises(InvalidPincodeError):
        normalise_pincode("45201")


def test_normalise_refuses_too_long() -> None:
    with pytest.raises(InvalidPincodeError):
        normalise_pincode("4520123")


def test_normalise_refuses_letters() -> None:
    with pytest.raises(InvalidPincodeError):
        normalise_pincode("45201A")


def test_normalise_refuses_a_leading_zero() -> None:
    with pytest.raises(InvalidPincodeError):
        normalise_pincode("052012")


def test_normalise_refuses_an_empty_string() -> None:
    with pytest.raises(InvalidPincodeError):
        normalise_pincode("")


# ---------------------------------------------------------------------------
# Is the pin served?
# ---------------------------------------------------------------------------
def test_a_pin_in_an_active_zone_is_served() -> None:
    zone = DeliveryZoneFactory(name="Indore")
    PinCodeZoneFactory(zone=zone, pincode="452012")

    assert is_pincode_served("452012") is True


def test_a_pin_in_an_inactive_zone_is_not_served() -> None:
    zone = DeliveryZoneFactory(name="Ujjain", is_active=False)
    PinCodeZoneFactory(zone=zone, pincode="456001")

    assert is_pincode_served("456001") is False


def test_an_unknown_pin_is_not_served() -> None:
    assert is_pincode_served("452099") is False


def test_an_invalid_pin_is_not_served_and_does_not_raise() -> None:
    assert is_pincode_served("not-a-pin") is False


def test_get_zone_for_pincode_returns_the_zone_or_none() -> None:
    active = DeliveryZoneFactory(name="Indore")
    PinCodeZoneFactory(zone=active, pincode="452012")
    inactive = DeliveryZoneFactory(name="Ujjain", is_active=False)
    PinCodeZoneFactory(zone=inactive, pincode="456001")

    assert get_zone_for_pincode("452012") == active
    assert get_zone_for_pincode("456001") is None
    assert get_zone_for_pincode("452099") is None


def test_several_pins_in_one_zone_are_all_served() -> None:
    zone = DeliveryZoneFactory(name="Indore")
    for pincode in ("452012", "452014", "453331"):
        PinCodeZoneFactory(zone=zone, pincode=pincode)

    assert is_pincode_served("452012") is True
    assert is_pincode_served("452014") is True
    assert is_pincode_served("453331") is True


def test_address_is_served_uses_the_address_pin_code() -> None:
    zone = DeliveryZoneFactory(name="Indore")
    PinCodeZoneFactory(zone=zone, pincode="452012")
    user = UserFactory()

    # Unsaved Address objects are enough: the service only reads ``pincode``.
    served = Address(user=user, pincode="452012")
    unserved = Address(user=user, pincode="452099")

    assert address_is_served(served) is True
    assert address_is_served(unserved) is False


def test_the_serviceability_check_uses_one_query() -> None:
    zone = DeliveryZoneFactory(name="Indore")
    PinCodeZoneFactory(zone=zone, pincode="452012")

    with CaptureQueriesContext(connection) as queries:
        assert is_pincode_served("452012") is True

    assert len(queries.captured_queries) == 1
