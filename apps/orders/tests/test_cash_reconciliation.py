"""Tests for the daily cash reconciliation (D79)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from apps.accounts.models import User
from apps.orders.models import CodCollection, Order
from apps.orders.staff_queries import cash_reconciliation
from apps.orders.tests.factories import OrderFactory
from apps.orders.tests.staff_helpers import DELIVERY_DAY, NOW, staff

pytestmark = pytest.mark.django_db


def _collection(order: Order, actor: User, amount: str, moment: datetime) -> CodCollection:
    """One cash collection row for an order."""
    return CodCollection.objects.create(
        order=order,
        amount=Decimal(amount),
        collected_by=actor,
        collected_at=moment,
    )


def test_reconciliation_sums_collected_per_collector() -> None:
    first_actor = staff()
    second_actor = staff()
    first_order = OrderFactory()
    second_order = OrderFactory()
    third_order = OrderFactory()
    _collection(first_order, first_actor, "100.00", NOW)
    _collection(second_order, first_actor, "50.00", NOW)
    _collection(third_order, second_actor, "25.00", NOW)

    result = cash_reconciliation(staff(), DELIVERY_DAY)

    totals = {row.collected_by.pk: row.total for row in result.collectors}
    assert totals == {first_actor.pk: Decimal("150.00"), second_actor.pk: Decimal("25.00")}
    counts = {row.collected_by.pk: row.collection_count for row in result.collectors}
    assert counts == {first_actor.pk: 2, second_actor.pk: 1}


def test_reconciliation_lists_out_for_delivery_cod_orders_as_unpaid() -> None:
    out_for_delivery = OrderFactory(status=Order.Status.OUT_FOR_DELIVERY)
    delivered = OrderFactory(status=Order.Status.DELIVERED, delivered_at=NOW)

    result = cash_reconciliation(staff(), DELIVERY_DAY)

    unpaid = [order.pk for order in result.unpaid_orders]
    assert out_for_delivery.pk in unpaid
    assert delivered.pk not in unpaid


def test_reconciliation_filters_by_day() -> None:
    order = OrderFactory()
    _collection(order, staff(), "100.00", NOW)

    result = cash_reconciliation(staff(), DELIVERY_DAY + timedelta(days=1))

    assert result.collectors == []
    assert result.grand_total == Decimal("0.00")


def test_reconciliation_excludes_other_days() -> None:
    actor = staff()
    today_order = OrderFactory()
    _collection(today_order, actor, "100.00", NOW)
    tomorrow_order = OrderFactory()
    _collection(tomorrow_order, actor, "70.00", NOW + timedelta(days=1))

    result = cash_reconciliation(staff(), DELIVERY_DAY)

    assert result.grand_total == Decimal("100.00")
    assert len(result.collectors) == 1


def test_reconciliation_has_a_grand_total() -> None:
    first_actor = staff()
    second_actor = staff()
    _collection(OrderFactory(), first_actor, "100.00", NOW)
    _collection(OrderFactory(), second_actor, "25.50", NOW)

    result = cash_reconciliation(staff(), DELIVERY_DAY)

    assert result.grand_total == Decimal("125.50")
