"""Tests for the staff order list query (D79)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import User
from apps.orders.models import Order
from apps.orders.staff_queries import list_staff_orders
from apps.orders.staff_services import (
    GATE_REASON_UNVERIFIED_EMAIL,
    GATE_REASON_UNVERIFIED_PHONE,
)
from apps.orders.tests.factories import OrderFactory
from apps.orders.tests.staff_helpers import DELIVERY_DAY, NOW, OTHER_DAY, staff
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_order_list_filters_by_status() -> None:
    actor = staff()
    confirmed = OrderFactory(status=Order.Status.CONFIRMED)
    OrderFactory(status=Order.Status.PACKING)

    rows = list_staff_orders(actor, status=Order.Status.CONFIRMED)

    assert [row.order.pk for row in rows] == [confirmed.pk]


def test_order_list_filters_by_delivery_day() -> None:
    actor = staff()
    first = OrderFactory(promised_delivery_date=DELIVERY_DAY)
    OrderFactory(promised_delivery_date=OTHER_DAY)

    rows = list_staff_orders(actor, delivery_day=DELIVERY_DAY)

    assert [row.order.pk for row in rows] == [first.pk]


def test_order_list_filters_by_payment_method() -> None:
    actor = staff()
    OrderFactory(payment_method=Order.PaymentMethod.COD)
    credit = OrderFactory(payment_method=Order.PaymentMethod.CREDIT)

    rows = list_staff_orders(actor, payment_method=Order.PaymentMethod.CREDIT)

    assert [row.order.pk for row in rows] == [credit.pk]


def test_order_list_searches_by_order_number() -> None:
    actor = staff()
    wanted = OrderFactory(order_number="BS/26-27/000101")
    OrderFactory(order_number="BS/26-27/000202")

    rows = list_staff_orders(actor, search="000101")

    assert [row.order.pk for row in rows] == [wanted.pk]


def test_order_list_searches_by_phone() -> None:
    actor = staff()
    customer = UserFactory(phone="+919000000001")
    wanted = OrderFactory(user=customer, ship_phone="+919000000001")
    OrderFactory(ship_phone="+919000000002")

    rows = list_staff_orders(actor, search="+919000000001")

    assert [row.order.pk for row in rows] == [wanted.pk]


def test_order_list_search_finds_the_phone_frozen_on_the_order() -> None:
    actor = staff()
    customer = UserFactory(phone="+919000000004")
    # The address phone of the order is not the phone of the account.
    wanted = OrderFactory(user=customer, ship_phone="+919000000005")
    other = OrderFactory(ship_phone="+919000000006")

    by_address_phone = list_staff_orders(actor, search="+919000000005")
    by_account_phone = list_staff_orders(actor, search="+919000000004")

    assert [row.order.pk for row in by_address_phone] == [wanted.pk]
    assert [row.order.pk for row in by_account_phone] == [wanted.pk]
    assert other.pk not in [row.order.pk for row in by_address_phone]
    assert other.pk not in [row.order.pk for row in by_account_phone]


def test_order_list_row_shows_customer_amount_method_and_verification() -> None:
    actor = staff()
    customer = UserFactory(full_name="Asha Sharma")
    OrderFactory(user=customer, ship_name="Asha Sharma", ship_phone="+919000000003")

    row = list_staff_orders(actor)[0]

    assert row.customer_name == "Asha Sharma"
    assert row.customer_phone == "+919000000003"
    assert row.grand_total == Decimal("230.00")
    assert row.amount_due_on_delivery == Decimal("230.00")
    assert row.payment_method == Order.PaymentMethod.COD
    assert row.phone_verified is True
    assert row.email_verified is True


def test_order_list_row_shows_gate_reason_for_unverified_phone() -> None:
    actor = staff()
    customer = UserFactory(phone_verified_at=None, email_verified_at=None)
    OrderFactory(user=customer, status=Order.Status.CONFIRMED)

    row = list_staff_orders(actor)[0]

    assert row.gate_reason == GATE_REASON_UNVERIFIED_PHONE
    assert "phone" in row.gate_reason


def test_order_list_row_shows_gate_reason_for_unverified_email() -> None:
    actor = staff()
    customer = UserFactory(phone_verified_at=NOW, email_verified_at=None)
    OrderFactory(user=customer, status=Order.Status.CONFIRMED)

    row = list_staff_orders(actor)[0]

    assert row.gate_reason == GATE_REASON_UNVERIFIED_EMAIL
    assert "email" in row.gate_reason


def test_order_list_row_has_no_gate_reason_when_verified() -> None:
    actor = staff()
    OrderFactory(status=Order.Status.CONFIRMED)

    row = list_staff_orders(actor)[0]

    assert row.gate_reason == ""


def test_order_list_query_count_is_flat() -> None:
    # A manager is not the owner, so the capability check really reads a row.
    actor = staff(User.ROLE_MANAGER)
    small_user = UserFactory()
    OrderFactory(user=small_user)
    big_user = UserFactory()
    OrderFactory(user=big_user)
    for _index in range(5):
        OrderFactory(user=big_user)

    # The first call warms the statement cache of the database connection.
    list_staff_orders(actor)

    with CaptureQueriesContext(connection) as small_queries:
        list_staff_orders(actor)
    with CaptureQueriesContext(connection) as big_queries:
        rows = list_staff_orders(actor)
        # One order for the small user, one for the big user and five more.
        assert len(rows) == 7

    assert len(small_queries.captured_queries) == len(big_queries.captured_queries)
