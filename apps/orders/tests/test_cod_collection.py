"""Tests for delivering an order and recording the cash at the door (D79)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.orders.models import CodCollection, Order
from apps.orders.services import InvalidTransitionError
from apps.orders.staff_services import (
    CodAmountMismatchError,
    CodAmountRequiredError,
    mark_delivered,
)
from apps.orders.tests.factories import OrderFactory
from apps.orders.tests.staff_helpers import NOW, staff

pytestmark = pytest.mark.django_db


def _out_for_delivery_order(**fields: object) -> Order:
    """An order that is already out for delivery."""
    values: dict[str, object] = {"status": Order.Status.OUT_FOR_DELIVERY}
    values.update(fields)
    return OrderFactory(**values)


def test_deliver_cod_records_amount_collector_and_time() -> None:
    order = _out_for_delivery_order()
    actor = staff()

    mark_delivered(order.pk, actor=actor, collected_amount=Decimal("230.00"), now=NOW)

    collection = CodCollection.objects.get(order=order)
    assert collection.amount == Decimal("230.00")
    assert collection.collected_by_id == actor.pk
    assert collection.collected_at == NOW


def test_deliver_cod_short_amount_refused() -> None:
    order = _out_for_delivery_order()

    # Nothing entered at the door is refused too: the staff member has to
    # write down what was collected.
    with pytest.raises(CodAmountRequiredError):
        mark_delivered(order.pk, actor=staff(), now=NOW)
    with pytest.raises(CodAmountMismatchError):
        mark_delivered(order.pk, actor=staff(), collected_amount=Decimal("229.00"), now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.OUT_FOR_DELIVERY
    assert not CodCollection.objects.exists()


def test_deliver_cod_over_amount_refused() -> None:
    order = _out_for_delivery_order()

    with pytest.raises(CodAmountMismatchError):
        mark_delivered(order.pk, actor=staff(), collected_amount=Decimal("231.00"), now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.OUT_FOR_DELIVERY
    assert not CodCollection.objects.exists()


def test_deliver_cod_second_collection_refused() -> None:
    order = _out_for_delivery_order()
    actor = staff()
    mark_delivered(order.pk, actor=actor, collected_amount=Decimal("230.00"), now=NOW)

    with pytest.raises(InvalidTransitionError):
        mark_delivered(order.pk, actor=actor, collected_amount=Decimal("230.00"), now=NOW)

    assert CodCollection.objects.filter(order=order).count() == 1


def test_deliver_split_order_collects_only_the_cash_part() -> None:
    # A khata part of 100.00 and the rest in cash at the door (D74).
    order = _out_for_delivery_order(
        payment_method=Order.PaymentMethod.CREDIT,
        credit_applied=Decimal("100.00"),
        amount_due_on_delivery=Decimal("130.00"),
    )

    mark_delivered(order.pk, actor=staff(), collected_amount=Decimal("130.00"), now=NOW)

    collection = CodCollection.objects.get(order=order)
    assert collection.amount == Decimal("130.00")


def test_deliver_credit_only_order_needs_no_cash() -> None:
    order = _out_for_delivery_order(payment_method=Order.PaymentMethod.CREDIT)

    mark_delivered(order.pk, actor=staff(), now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.DELIVERED
    assert not CodCollection.objects.exists()


def test_deliver_online_paid_order_needs_no_cash() -> None:
    order = _out_for_delivery_order(
        payment_method=Order.PaymentMethod.ONLINE,
        online_amount=Decimal("230.00"),
        amount_due_on_delivery=Decimal("0.00"),
    )

    mark_delivered(order.pk, actor=staff(), now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.DELIVERED
    assert not CodCollection.objects.exists()


def test_deliver_order_with_no_cash_due_refuses_a_cash_amount() -> None:
    # A khata order owes nothing at the door, so there is no cash to collect
    # and an amount must not be accepted for it.
    order = _out_for_delivery_order(payment_method=Order.PaymentMethod.CREDIT)

    with pytest.raises(CodAmountMismatchError):
        mark_delivered(
            order.pk,
            actor=staff(),
            collected_amount=Decimal("230.00"),
            now=NOW,
        )

    order.refresh_from_db()
    assert order.status == Order.Status.OUT_FOR_DELIVERY
    assert not CodCollection.objects.exists()


def test_deliver_moves_to_delivered() -> None:
    order = _out_for_delivery_order()

    mark_delivered(order.pk, actor=staff(), collected_amount=Decimal("230.00"), now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.DELIVERED
    assert order.delivered_at == NOW


def test_deliver_refused_when_not_out_for_delivery() -> None:
    order = OrderFactory(status=Order.Status.PACKING)

    with pytest.raises(InvalidTransitionError):
        mark_delivered(order.pk, actor=staff(), collected_amount=Decimal("230.00"), now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.PACKING
    assert not CodCollection.objects.exists()


def test_collection_is_unique_per_order() -> None:
    order = _out_for_delivery_order()
    actor = staff()
    CodCollection.objects.create(
        order=order,
        amount=Decimal("230.00"),
        collected_by=actor,
        collected_at=NOW,
    )

    with pytest.raises(IntegrityError):
        CodCollection.objects.create(
            order=order,
            amount=Decimal("10.00"),
            collected_by=actor,
            collected_at=NOW,
        )


def test_collection_amount_must_be_positive() -> None:
    order = _out_for_delivery_order()
    collection = CodCollection(
        order=order,
        amount=Decimal("0.00"),
        collected_by=staff(),
        collected_at=NOW,
    )

    with pytest.raises(ValidationError) as error:
        collection.full_clean()

    # A database check constraint is reported under the "__all__" key.
    assert "__all__" in error.value.error_dict


def test_deliver_is_atomic_status_and_cash_together() -> None:
    order = _out_for_delivery_order()

    # The status move fails after the cash row was written, so the cash row
    # must be rolled back with it.
    with (
        patch(
            "apps.orders.staff_services.transition_order",
            side_effect=InvalidTransitionError("boom"),
        ),
        pytest.raises(InvalidTransitionError),
    ):
        mark_delivered(order.pk, actor=staff(), collected_amount=Decimal("230.00"), now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.OUT_FOR_DELIVERY
    assert not CodCollection.objects.exists()
