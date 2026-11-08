"""Tests for the one customer-cancel rule (Slice 38d, D70)."""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.orders.models import Order
from apps.orders.services import (
    CUSTOMER_CANCELLABLE,
    InvalidTransitionError,
    cancel_order_by_customer,
)
from apps.orders.tests.factories import OrderFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _status_kwargs(status: str) -> dict[str, object]:
    """The timestamps the check constraints require for these statuses."""
    if status == Order.Status.CANCELLED:
        return {"cancelled_at": timezone.now()}
    if status == Order.Status.DELIVERED:
        return {"delivered_at": timezone.now()}
    return {}


def test_customer_cancellable_statuses_are_placed_and_confirmed() -> None:
    assert CUSTOMER_CANCELLABLE == (Order.Status.PLACED, Order.Status.CONFIRMED)


def test_customer_cancel_refuses_every_other_status() -> None:
    user = UserFactory()
    # A comprehension keeps every status the customer may not cancel.
    others = [status for status in Order.Status.values if status not in CUSTOMER_CANCELLABLE]

    for status in others:
        order = OrderFactory(user=user, status=status, **_status_kwargs(status))

        with pytest.raises(InvalidTransitionError):
            cancel_order_by_customer(user, order.pk)

        order.refresh_from_db()
        assert order.status == status
        assert order.cancel_reason == ""
