"""Tests for reversing a credit charge when an order is cancelled (D68)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.credit.models import CreditAccount, CreditEntry
from apps.credit.services import available_credit
from apps.orders.models import Order
from apps.orders.services import (
    CancelReasonRequiredError,
    InvalidTransitionError,
    cancel_order_by_customer,
    transition_order,
)
from apps.orders.state import DELIVERED, OUT_FOR_DELIVERY, PACKING
from apps.orders.tests.checkout_helpers import (
    NOW,
    address,
    clear_settings,
    credit_user,
    fill_cart,
    place_cod,
    place_credit,
    reset_settings,
    serve,
    variant,
)
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before every test."""
    reset_settings()
    yield
    clear_settings()


def _credit_order(limit: str = "1000.00"):
    """A real credit order for a served customer with stock in the cart."""
    user = credit_user(limit=limit)
    serve()
    address_row = address(user)
    fill_cart(user, variant())
    order = place_credit(user, address_row).order
    return user, order


def _cod_order(limit: str = "1000.00"):
    """A real COD order for a customer who also has a credit account."""
    user = credit_user(limit=limit)
    serve()
    address_row = address(user)
    fill_cart(user, variant())
    order = place_cod(user, address_row).order
    return user, order


def _reversal(order: Order) -> CreditEntry:
    """The single cancellation reversal of this order."""
    return CreditEntry.objects.get(
        order=order,
        entry_type=CreditEntry.EntryType.CANCEL_REVERSAL,
    )


def test_staff_cancel_of_a_credit_order_reverses_the_charge() -> None:
    user, order = _credit_order()
    staff = UserFactory()

    transition_order(order.pk, Order.Status.CANCELLED, actor=staff, reason="Out of stock", now=NOW)

    reversal = _reversal(order)
    assert reversal.amount == Decimal("-230.00")
    assert reversal.balance_after == Decimal("0.00")
    assert CreditAccount.objects.get(user=user).balance == Decimal("0.00")


def test_customer_cancel_of_a_credit_order_reverses_the_charge() -> None:
    user, order = _credit_order()

    cancel_order_by_customer(user, order.pk, now=NOW)

    assert _reversal(order).amount == Decimal("-230.00")
    assert CreditAccount.objects.get(user=user).balance == Decimal("0.00")


def test_cancel_after_packing_started_reverses_the_charge() -> None:
    _user, order = _credit_order()
    staff = UserFactory()
    transition_order(order.pk, PACKING, actor=staff, now=NOW)

    transition_order(order.pk, Order.Status.CANCELLED, actor=staff, reason="Damaged", now=NOW)

    assert _reversal(order).amount == Decimal("-230.00")


def test_cancel_from_out_for_delivery_reverses_the_charge() -> None:
    _user, order = _credit_order()
    staff = UserFactory()
    transition_order(order.pk, PACKING, actor=staff, now=NOW)
    transition_order(order.pk, OUT_FOR_DELIVERY, actor=staff, now=NOW)

    transition_order(order.pk, Order.Status.CANCELLED, actor=staff, reason="Refused", now=NOW)

    assert _reversal(order).amount == Decimal("-230.00")


def test_cancel_restores_the_available_credit() -> None:
    user, order = _credit_order(limit="1000.00")
    assert available_credit(user) == Decimal("770.00")

    cancel_order_by_customer(user, order.pk, now=NOW)

    assert available_credit(user) == Decimal("1000.00")


def test_cancel_of_a_cod_order_writes_no_credit_entry() -> None:
    _user, order = _cod_order()
    staff = UserFactory()

    transition_order(order.pk, Order.Status.CANCELLED, actor=staff, reason="Out of stock", now=NOW)

    assert CreditEntry.objects.filter(order=order).count() == 0


def test_cancel_twice_is_refused_and_reverses_only_once() -> None:
    _user, order = _credit_order()
    staff = UserFactory()
    transition_order(order.pk, Order.Status.CANCELLED, actor=staff, reason="First", now=NOW)

    with pytest.raises(InvalidTransitionError):
        transition_order(order.pk, Order.Status.CANCELLED, actor=staff, reason="Again", now=NOW)

    assert (
        CreditEntry.objects.filter(
            order=order,
            entry_type=CreditEntry.EntryType.CANCEL_REVERSAL,
        ).count()
        == 1
    )


def test_delivered_credit_order_cannot_be_cancelled_and_keeps_the_charge() -> None:
    user, order = _credit_order()
    staff = UserFactory()
    transition_order(order.pk, PACKING, actor=staff, now=NOW)
    transition_order(order.pk, OUT_FOR_DELIVERY, actor=staff, now=NOW)
    transition_order(order.pk, DELIVERED, actor=staff, now=NOW)

    with pytest.raises(InvalidTransitionError):
        transition_order(order.pk, Order.Status.CANCELLED, actor=staff, reason="Too late", now=NOW)

    assert CreditEntry.objects.filter(order=order).count() == 1
    assert CreditAccount.objects.get(user=user).balance == Decimal("230.00")


def test_failed_cancel_keeps_the_charge() -> None:
    user, order = _credit_order()
    staff = UserFactory()

    with pytest.raises(CancelReasonRequiredError):
        transition_order(order.pk, Order.Status.CANCELLED, actor=staff, reason="   ", now=NOW)

    assert CreditEntry.objects.filter(order=order).count() == 1
    assert CreditAccount.objects.get(user=user).balance == Decimal("230.00")


def test_cancel_reversal_entry_is_linked_to_the_order_and_actor() -> None:
    _user, order = _credit_order()
    staff = UserFactory()

    transition_order(order.pk, Order.Status.CANCELLED, actor=staff, reason="Out of stock", now=NOW)

    reversal = _reversal(order)
    assert reversal.order_id == order.pk
    assert reversal.created_by_id == staff.pk
    assert reversal.entry_type == CreditEntry.EntryType.CANCEL_REVERSAL
