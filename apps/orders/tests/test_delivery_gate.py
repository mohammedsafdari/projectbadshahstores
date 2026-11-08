"""Tests for the delivery gate at OUT_FOR_DELIVERY (D71)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.orders.models import OrderStatusLog
from apps.orders.services import CustomerNotVerifiedError, OrderError, transition_order
from apps.orders.state import CANCELLED, CONFIRMED, OUT_FOR_DELIVERY, PACKING
from apps.orders.tests.factories import OrderFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _packing_order(user):
    return OrderFactory(user=user, status=PACKING)


def _unverified_user():
    return UserFactory(phone_verified_at=None, email_verified_at=None)


def test_out_for_delivery_is_refused_for_an_unverified_phone() -> None:
    user = UserFactory(phone_verified_at=None, email_verified_at=timezone.now())
    order = _packing_order(user)

    with pytest.raises(CustomerNotVerifiedError):
        transition_order(order.pk, OUT_FOR_DELIVERY, actor=UserFactory())


def test_out_for_delivery_is_refused_when_the_email_is_not_verified() -> None:
    user = UserFactory(phone_verified_at=timezone.now(), email_verified_at=None)
    order = _packing_order(user)

    with pytest.raises(CustomerNotVerifiedError):
        transition_order(order.pk, OUT_FOR_DELIVERY, actor=UserFactory())


def test_out_for_delivery_is_allowed_when_phone_and_email_are_verified() -> None:
    user = UserFactory()
    order = _packing_order(user)

    result = transition_order(order.pk, OUT_FOR_DELIVERY, actor=UserFactory())

    assert result.status == OUT_FOR_DELIVERY


def test_out_for_delivery_is_allowed_without_an_email_when_the_phone_is_verified() -> None:
    user = UserFactory(
        email=None,
        phone_verified_at=timezone.now(),
        email_verified_at=None,
    )
    order = _packing_order(user)

    result = transition_order(order.pk, OUT_FOR_DELIVERY, actor=UserFactory())

    assert result.status == OUT_FOR_DELIVERY


def test_refused_delivery_changes_nothing_and_sends_no_notice() -> None:
    user = _unverified_user()
    order = _packing_order(user)

    with (
        patch("apps.orders.services.notify_order_event") as notify,
        pytest.raises(CustomerNotVerifiedError),
    ):
        transition_order(order.pk, OUT_FOR_DELIVERY, actor=UserFactory())

    order.refresh_from_db()
    assert order.status == PACKING
    assert not OrderStatusLog.objects.filter(
        order=order,
        to_status=OUT_FOR_DELIVERY,
    ).exists()
    notify.assert_not_called()


def test_gate_does_not_apply_to_packing() -> None:
    user = _unverified_user()
    order = OrderFactory(user=user, status=CONFIRMED)

    result = transition_order(order.pk, PACKING, actor=UserFactory())

    assert result.status == PACKING


def test_gate_does_not_apply_to_cancel() -> None:
    user = _unverified_user()
    order = _packing_order(user)

    result = transition_order(
        order.pk,
        CANCELLED,
        actor=UserFactory(),
        reason="Out of stock",
    )

    assert result.status == CANCELLED


def test_gate_error_is_an_order_error() -> None:
    assert issubclass(CustomerNotVerifiedError, OrderError)


def test_verifying_later_lets_the_same_order_go_out() -> None:
    user = _unverified_user()
    order = _packing_order(user)
    with pytest.raises(CustomerNotVerifiedError):
        transition_order(order.pk, OUT_FOR_DELIVERY, actor=UserFactory())

    user.phone_verified_at = timezone.now()
    user.email_verified_at = timezone.now()
    user.save(update_fields=["phone_verified_at", "email_verified_at"])

    result = transition_order(order.pk, OUT_FOR_DELIVERY, actor=UserFactory())

    assert result.status == OUT_FOR_DELIVERY
