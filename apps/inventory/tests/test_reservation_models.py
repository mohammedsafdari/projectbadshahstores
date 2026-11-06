"""Tests for the StockReservation model and its database constraints (D63)."""

from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import IntegrityError
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.models import StockReservation
from apps.inventory.tests.factories import StockReservationFactory

pytestmark = pytest.mark.django_db


def test_quantity_must_be_positive() -> None:
    with pytest.raises(IntegrityError):
        StockReservationFactory(quantity=Decimal("0"))


def test_a_variant_appears_once_per_group() -> None:
    group = uuid4()
    first = StockReservationFactory(checkout_group=group)

    with pytest.raises(IntegrityError):
        StockReservationFactory(checkout_group=group, variant=first.variant)


def test_the_same_variant_can_be_reserved_in_two_groups() -> None:
    variant = ProductVariantFactory()
    first = StockReservationFactory(variant=variant)
    second = StockReservationFactory(variant=variant)

    assert first.checkout_group != second.checkout_group
    assert StockReservation.objects.filter(variant=variant).count() == 2


def test_checkout_kind_needs_an_expiry_time() -> None:
    with pytest.raises(IntegrityError):
        StockReservationFactory(
            kind=StockReservation.Kind.CHECKOUT_PENDING_PAYMENT,
            expires_at=None,
        )


def test_held_kind_must_not_have_an_expiry_time() -> None:
    with pytest.raises(IntegrityError):
        StockReservationFactory(
            kind=StockReservation.Kind.HELD_FOR_FULFILMENT,
            expires_at=timezone.now(),
        )


def test_consumed_at_is_set_only_for_consumed_rows() -> None:
    with pytest.raises(IntegrityError):
        StockReservationFactory(
            status=StockReservation.Status.ACTIVE,
            consumed_at=timezone.now(),
        )


def test_released_at_is_set_only_for_released_rows() -> None:
    with pytest.raises(IntegrityError):
        StockReservationFactory(
            status=StockReservation.Status.ACTIVE,
            released_at=timezone.now(),
        )


def test_deleting_a_variant_with_reservations_is_refused() -> None:
    reservation = StockReservationFactory()

    with pytest.raises(ProtectedError):
        reservation.variant.delete()


def test_deleting_a_user_with_reservations_is_refused() -> None:
    reservation = StockReservationFactory()

    with pytest.raises(ProtectedError):
        reservation.user.delete()


def test_quantity_keeps_three_decimals() -> None:
    reservation = StockReservationFactory(quantity=Decimal("2.5"))
    reservation.refresh_from_db()

    assert reservation.quantity == Decimal("2.500")
    assert reservation.quantity.as_tuple().exponent == -3


def test_constraints_have_their_documented_names() -> None:
    names = {constraint.name for constraint in StockReservation._meta.constraints}

    assert names == {
        "stockreservation_quantity_gt_0",
        "stockreservation_unique_variant_per_group",
        "stockreservation_expiry_matches_kind",
        "stockreservation_consumed_at_matches_status",
        "stockreservation_released_at_matches_status",
    }


def test_timestamps_are_set() -> None:
    reservation = StockReservationFactory()

    assert reservation.created_at is not None
    assert reservation.updated_at is not None
