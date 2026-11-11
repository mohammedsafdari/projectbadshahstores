"""Tests for the pick list of one delivery day (D79)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.catalog.tests.factories import ProductVariantFactory
from apps.orders.models import Order
from apps.orders.staff_queries import pick_list_for_day
from apps.orders.tests.factories import OrderFactory, OrderItemFactory
from apps.orders.tests.staff_helpers import DELIVERY_DAY, NOW, OTHER_DAY, staff

pytestmark = pytest.mark.django_db


def test_pick_list_groups_items_by_order() -> None:
    actor = staff()
    first = OrderFactory(order_number="BS/26-27/000301", status=Order.Status.CONFIRMED)
    OrderItemFactory(order=first)
    OrderItemFactory(order=first)
    second = OrderFactory(order_number="BS/26-27/000302", status=Order.Status.CONFIRMED)
    OrderItemFactory(order=second)

    result = pick_list_for_day(actor, DELIVERY_DAY)

    assert [entry.order_number for entry in result.orders] == [
        "BS/26-27/000301",
        "BS/26-27/000302",
    ]
    assert [len(entry.lines) for entry in result.orders] == [2, 1]


def test_pick_list_shows_pack_labels_and_quantities() -> None:
    actor = staff()
    order = OrderFactory(order_number="BS/26-27/000311", status=Order.Status.CONFIRMED)
    item = OrderItemFactory(order=order, quantity_ordered=Decimal("3"))

    result = pick_list_for_day(actor, DELIVERY_DAY)

    line = result.orders[0].lines[0]
    assert line.product_name == item.product_name
    assert line.pack_label == item.pack_label
    assert line.quantity == Decimal("3")


def test_pick_list_totals_per_variant_across_orders() -> None:
    actor = staff()
    variant = ProductVariantFactory()
    first = OrderFactory(order_number="BS/26-27/000321", status=Order.Status.CONFIRMED)
    OrderItemFactory(order=first, variant=variant, quantity_ordered=Decimal("2"))
    second = OrderFactory(order_number="BS/26-27/000322", status=Order.Status.CONFIRMED)
    OrderItemFactory(order=second, variant=variant, quantity_ordered=Decimal("3"))

    result = pick_list_for_day(actor, DELIVERY_DAY)

    assert len(result.totals) == 1
    assert result.totals[0].variant_id == variant.pk
    assert result.totals[0].quantity == Decimal("5")
    assert result.totals[0].order_count == 2


def test_pick_list_excludes_cancelled_orders() -> None:
    actor = staff()
    OrderFactory(
        order_number="BS/26-27/000331",
        status=Order.Status.CANCELLED,
        cancelled_at=NOW,
    )

    result = pick_list_for_day(actor, DELIVERY_DAY)

    assert result.orders == []


def test_pick_list_includes_only_confirmed_and_packing_orders() -> None:
    actor = staff()
    confirmed = OrderFactory(order_number="BS/26-27/000341", status=Order.Status.CONFIRMED)
    packing = OrderFactory(order_number="BS/26-27/000342", status=Order.Status.PACKING)
    OrderFactory(order_number="BS/26-27/000343", status=Order.Status.OUT_FOR_DELIVERY)
    OrderFactory(
        order_number="BS/26-27/000344",
        status=Order.Status.DELIVERED,
        delivered_at=NOW,
    )

    result = pick_list_for_day(actor, DELIVERY_DAY)

    picked = {entry.order.pk for entry in result.orders}
    assert picked == {confirmed.pk, packing.pk}


def test_pick_list_empty_day_returns_empty() -> None:
    actor = staff()
    OrderFactory(order_number="BS/26-27/000351", status=Order.Status.CONFIRMED)

    result = pick_list_for_day(actor, OTHER_DAY)

    assert result.orders == []
    assert result.totals == []


def test_pick_list_orders_are_stable_by_order_number() -> None:
    actor = staff()
    OrderFactory(order_number="BS/26-27/000363", status=Order.Status.CONFIRMED)
    OrderFactory(order_number="BS/26-27/000361", status=Order.Status.CONFIRMED)
    OrderFactory(order_number="BS/26-27/000362", status=Order.Status.CONFIRMED)

    result = pick_list_for_day(actor, DELIVERY_DAY)

    assert [entry.order_number for entry in result.orders] == [
        "BS/26-27/000361",
        "BS/26-27/000362",
        "BS/26-27/000363",
    ]
