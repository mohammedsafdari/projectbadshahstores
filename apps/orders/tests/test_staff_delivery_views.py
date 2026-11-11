"""View tests for the staff delivery and cash screens (D79 amendment)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.orders.models import CodCollection, Order
from apps.orders.tests.factories import OrderFactory
from apps.orders.tests.staff_helpers import staff

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def test_delivery_list_shows_only_out_for_delivery_today() -> None:
    today = OrderFactory(
        order_number="BS/26-27/000911",
        status=Order.Status.OUT_FOR_DELIVERY,
        promised_delivery_date=TODAY,
    )
    OrderFactory(
        order_number="BS/26-27/000912",
        status=Order.Status.CONFIRMED,
        promised_delivery_date=TODAY,
    )
    OrderFactory(
        order_number="BS/26-27/000913",
        status=Order.Status.OUT_FOR_DELIVERY,
        promised_delivery_date=TODAY + timedelta(days=1),
    )

    html = _client(staff(User.ROLE_DELIVERY)).get(reverse("staff_delivery_list")).content.decode()

    assert today.order_number in html
    assert "BS/26-27/000912" not in html
    assert "BS/26-27/000913" not in html


def test_delivery_list_shows_amount_to_collect() -> None:
    OrderFactory(
        status=Order.Status.OUT_FOR_DELIVERY,
        promised_delivery_date=TODAY,
        amount_due_on_delivery=Decimal("230.00"),
    )

    html = _client(staff(User.ROLE_DELIVERY)).get(reverse("staff_delivery_list")).content.decode()

    assert "230.00" in html


def test_delivery_role_sees_delivery_and_cash_only() -> None:
    client = _client(staff(User.ROLE_DELIVERY))

    assert client.get(reverse("staff_delivery_list")).status_code == 200
    assert client.get(reverse("staff_cash")).status_code == 200
    assert client.get(reverse("staff_order_list")).status_code == 403
    assert client.get(reverse("staff_pick_list")).status_code == 403
    assert client.get(reverse("staff_credit_dues")).status_code == 403


def test_mark_delivered_post_records_collection() -> None:
    actor = staff(User.ROLE_DELIVERY)
    order = OrderFactory(
        status=Order.Status.OUT_FOR_DELIVERY,
        promised_delivery_date=TODAY,
    )

    response = _client(actor).post(
        reverse("staff_mark_delivered", args=[order.order_number]),
        {"collected_amount": "230.00"},
    )

    assert response.status_code == 302
    order.refresh_from_db()
    assert order.status == Order.Status.DELIVERED
    collection = CodCollection.objects.get(order=order)
    assert collection.amount == Decimal("230.00")
    assert collection.collected_by_id == actor.pk


def test_mark_delivered_wrong_amount_shows_message() -> None:
    order = OrderFactory(
        status=Order.Status.OUT_FOR_DELIVERY,
        promised_delivery_date=TODAY,
    )

    response = _client(staff(User.ROLE_DELIVERY)).post(
        reverse("staff_mark_delivered", args=[order.order_number]),
        {"collected_amount": "100.00"},
    )

    assert response.status_code == 200
    assert "exactly" in response.content.decode()
    order.refresh_from_db()
    assert order.status == Order.Status.OUT_FOR_DELIVERY
    assert not CodCollection.objects.filter(order=order).exists()


def test_mark_delivered_without_capability_is_403() -> None:
    order = OrderFactory(
        status=Order.Status.OUT_FOR_DELIVERY,
        promised_delivery_date=TODAY,
    )

    response = _client(staff(User.ROLE_PACKER)).post(
        reverse("staff_mark_delivered", args=[order.order_number]),
        {"collected_amount": "230.00"},
    )

    assert response.status_code == 403
    order.refresh_from_db()
    assert order.status == Order.Status.OUT_FOR_DELIVERY


def test_cash_page_shows_totals_per_collector() -> None:
    actor = staff(User.ROLE_DELIVERY)
    for amount in ("100.00", "50.00"):
        CodCollection.objects.create(
            order=OrderFactory(),
            amount=Decimal(amount),
            collected_by=actor,
            collected_at=timezone.now(),
        )

    html = _client(actor).get(reverse("staff_cash")).content.decode()

    assert "150.00" in html
    assert actor.phone in html


def test_cash_page_day_picker_changes_the_day() -> None:
    actor = staff(User.ROLE_DELIVERY)
    CodCollection.objects.create(
        order=OrderFactory(),
        amount=Decimal("100.00"),
        collected_by=actor,
        collected_at=timezone.now(),
    )

    html = (
        _client(actor)
        .get(
            reverse("staff_cash"),
            {"day": "2020-01-01"},
        )
        .content.decode()
    )

    assert "No cash collected on this day." in html
    assert "100.00" not in html


def test_cash_page_lists_unpaid_cod_orders() -> None:
    order = OrderFactory(
        order_number="BS/26-27/000921",
        status=Order.Status.OUT_FOR_DELIVERY,
    )

    html = _client(staff(User.ROLE_DELIVERY)).get(reverse("staff_cash")).content.decode()

    assert order.order_number in html


def test_packer_cannot_open_cash_page() -> None:
    response = _client(staff(User.ROLE_PACKER)).get(reverse("staff_cash"))

    assert response.status_code == 403


def test_delivery_list_shows_overdue_orders_from_earlier_days() -> None:
    overdue = OrderFactory(
        order_number="BS/26-27/000914",
        status=Order.Status.OUT_FOR_DELIVERY,
        promised_delivery_date=TODAY - timedelta(days=2),
    )

    html = _client(staff(User.ROLE_DELIVERY)).get(reverse("staff_delivery_list")).content.decode()

    assert overdue.order_number in html
    assert overdue.promised_delivery_date.isoformat() in html


def test_delivery_list_excludes_orders_promised_for_a_future_day() -> None:
    future = OrderFactory(
        order_number="BS/26-27/000915",
        status=Order.Status.OUT_FOR_DELIVERY,
        promised_delivery_date=TODAY + timedelta(days=1),
    )

    html = _client(staff(User.ROLE_DELIVERY)).get(reverse("staff_delivery_list")).content.decode()

    assert future.order_number not in html


def test_delivery_list_orders_overdue_first_then_by_number() -> None:
    oldest_high = OrderFactory(
        order_number="BS/26-27/000999",
        status=Order.Status.OUT_FOR_DELIVERY,
        promised_delivery_date=TODAY - timedelta(days=2),
    )
    oldest_low = OrderFactory(
        order_number="BS/26-27/000998",
        status=Order.Status.OUT_FOR_DELIVERY,
        promised_delivery_date=TODAY - timedelta(days=2),
    )
    today = OrderFactory(
        order_number="BS/26-27/000001",
        status=Order.Status.OUT_FOR_DELIVERY,
        promised_delivery_date=TODAY,
    )

    html = _client(staff(User.ROLE_DELIVERY)).get(reverse("staff_delivery_list")).content.decode()

    assert html.index(oldest_low.order_number) < html.index(oldest_high.order_number)
    assert html.index(oldest_high.order_number) < html.index(today.order_number)
