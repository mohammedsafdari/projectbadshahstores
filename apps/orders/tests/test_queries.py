"""Tests for the two order read helpers (D64)."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.orders.models import OrderStatusLog
from apps.orders.services import (
    OrderNotFoundError,
    get_user_order,
    get_user_order_by_group,
    list_user_orders,
)
from apps.orders.tests.factories import OrderFactory, OrderItemFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _log(order) -> OrderStatusLog:
    return OrderStatusLog.objects.create(
        order=order,
        from_status="",
        to_status="PLACED",
        note="Order placed",
    )


def test_get_user_order_returns_the_order_with_items_and_logs() -> None:
    user = UserFactory()
    order = OrderFactory(user=user)
    OrderItemFactory(order=order)
    _log(order)

    result = get_user_order(user, order.pk)

    assert result.pk == order.pk
    assert len(result.items.all()) == 1
    assert len(result.status_logs.all()) == 1


def test_get_user_order_prefetches_so_the_query_count_does_not_grow() -> None:
    small_user = UserFactory()
    small = OrderFactory(user=small_user)
    OrderItemFactory(order=small)
    _log(small)

    big_user = UserFactory()
    big = OrderFactory(user=big_user)
    for _index in range(5):
        OrderItemFactory(order=big)
    _log(big)

    # Warm the connection's statement cache with the same three queries.
    get_user_order(small_user, small.pk)

    with CaptureQueriesContext(connection) as small_queries:
        get_user_order(small_user, small.pk)
    with CaptureQueriesContext(connection) as big_queries:
        result = get_user_order(big_user, big.pk)
        assert len(result.items.all()) == 5
        assert len(result.status_logs.all()) == 1

    assert len(small_queries.captured_queries) == len(big_queries.captured_queries)


def test_get_user_order_hides_another_customers_order() -> None:
    owner = UserFactory()
    order = OrderFactory(user=owner)

    with pytest.raises(OrderNotFoundError):
        get_user_order(UserFactory(), order.pk)


def test_get_user_order_hides_a_missing_order() -> None:
    with pytest.raises(OrderNotFoundError):
        get_user_order(UserFactory(), 999999)


def test_list_user_orders_returns_only_this_users_orders_newest_first() -> None:
    user = UserFactory()
    first = OrderFactory(user=user)
    second = OrderFactory(user=user)
    other = OrderFactory()

    orders = list(list_user_orders(user))

    assert [order.pk for order in orders] == [second.pk, first.pk]
    assert other.pk not in [order.pk for order in orders]


def test_get_user_order_by_group_returns_own_order() -> None:
    user = UserFactory()
    order = OrderFactory(user=user)
    OrderItemFactory(order=order)
    _log(order)

    result = get_user_order_by_group(user, order.checkout_group)

    assert result.pk == order.pk
    assert len(result.items.all()) == 1
    assert len(result.status_logs.all()) == 1


def test_get_user_order_by_group_hides_another_users_order() -> None:
    owner = UserFactory()
    order = OrderFactory(user=owner)

    with pytest.raises(OrderNotFoundError):
        get_user_order_by_group(UserFactory(), order.checkout_group)
