"""Tests for the delivery models (Slice 33, D62)."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.accounts.models import PINCODE_VALIDATOR as ADDRESS_PINCODE_VALIDATOR
from apps.delivery.models import PINCODE_VALIDATOR, DeliveryZone, PinCodeZone
from apps.delivery.tests.factories import DeliveryZoneFactory, PinCodeZoneFactory

pytestmark = pytest.mark.django_db


def test_zone_name_is_unique() -> None:
    DeliveryZoneFactory(name="Indore")

    with pytest.raises(IntegrityError), transaction.atomic():
        DeliveryZoneFactory(name="Indore")

    assert DeliveryZone.objects.filter(name="Indore").count() == 1


def test_a_pin_code_belongs_to_one_zone_only() -> None:
    first = DeliveryZoneFactory(name="Indore")
    second = DeliveryZoneFactory(name="Ujjain")
    PinCodeZoneFactory(zone=first, pincode="452012")

    with pytest.raises(IntegrityError), transaction.atomic():
        PinCodeZoneFactory(zone=second, pincode="452012")

    assert PinCodeZone.objects.filter(pincode="452012").count() == 1


def test_pin_code_must_be_six_digits_not_starting_with_zero() -> None:
    zone = DeliveryZoneFactory()
    bad = PinCodeZone(zone=zone, pincode="052012")

    with pytest.raises(ValidationError):
        bad.full_clean()

    good = PinCodeZone(zone=zone, pincode="452012")
    good.full_clean()


def test_pin_code_validator_matches_the_address_validator() -> None:
    # The good samples must pass both callables; the bad ones must fail both
    # with the same message. This is the drift test for the copied validator.
    for sample in ("452012", "453331"):
        assert PINCODE_VALIDATOR(sample) == ADDRESS_PINCODE_VALIDATOR(sample)

    for sample in ("052012", "45201", "abcdef", ""):
        with pytest.raises(ValidationError) as delivery_error:
            PINCODE_VALIDATOR(sample)
        with pytest.raises(ValidationError) as address_error:
            ADDRESS_PINCODE_VALIDATOR(sample)
        assert delivery_error.value.messages == address_error.value.messages


def test_a_zone_with_pin_codes_cannot_be_deleted() -> None:
    zone = DeliveryZoneFactory()
    PinCodeZoneFactory(zone=zone, pincode="452012")

    with pytest.raises(ProtectedError):
        zone.delete()

    assert DeliveryZone.objects.filter(pk=zone.pk).exists()


def test_zone_is_active_by_default() -> None:
    zone = DeliveryZoneFactory()

    assert zone.is_active is True


def test_constraints_have_their_documented_names() -> None:
    zone_names = {constraint.name for constraint in DeliveryZone._meta.constraints}
    pin_names = {constraint.name for constraint in PinCodeZone._meta.constraints}

    assert zone_names == {"delivery_zone_unique_name"}
    assert pin_names == {"pincode_zone_unique_pincode"}


def test_timestamps_are_set() -> None:
    zone = DeliveryZoneFactory()

    assert zone.created_at is not None
    assert zone.updated_at is not None


def test_zone_str_is_its_name() -> None:
    zone = DeliveryZoneFactory(name="Indore")

    assert str(zone) == "Indore"


def test_pin_code_str_shows_the_pin_code_and_zone() -> None:
    zone = DeliveryZoneFactory(name="Indore")
    pin = PinCodeZoneFactory(zone=zone, pincode="452012")

    assert str(pin) == "452012 (Indore)"
