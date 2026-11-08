"""Tests for the "My orders" read helper (D70)."""

from __future__ import annotations

import pytest

from apps.orders.services import list_user_order_rows
from apps.orders.tests.factories import OrderFactory, OrderItemFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_list_user_order_rows_counts_items_for_each_order() -> None:
    user = UserFactory()
    first = OrderFactory(user=user)
    second = OrderFactory(user=user)
    OrderItemFactory(order=first)
    OrderItemFactory(order=first)
    OrderItemFactory(order=second)

    # A dict comprehension maps each row's primary key to the row itself.
    rows = {row.pk: row for row in list_user_order_rows(user)}

    assert rows[first.pk].item_count == 2
    assert rows[second.pk].item_count == 1


def test_list_user_order_rows_are_newest_first_and_only_this_customers() -> None:
    user = UserFactory()
    first = OrderFactory(user=user)
    second = OrderFactory(user=user)
    other = OrderFactory()

    rows = list(list_user_order_rows(user))

    assert [row.pk for row in rows] == [second.pk, first.pk]
    assert other.pk not in [row.pk for row in rows]
