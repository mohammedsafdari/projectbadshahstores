"""View tests for the staff order screens (D79 amendment)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.capabilities import Capability
from apps.accounts.models import RoleCapability, User
from apps.inventory.models import InventoryMovement
from apps.inventory.services import adjust_stock
from apps.orders.models import Order, OrderStatusLog
from apps.orders.tests.factories import OrderFactory, OrderItemFactory
from apps.orders.tests.staff_helpers import DELIVERY_DAY, order_with_hold, staff
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _view_orders_only() -> User:
    viewer = staff(User.ROLE_MEMBER)
    RoleCapability.objects.create(
        role=User.ROLE_MEMBER,
        capability=Capability.VIEW_ORDERS,
        allowed=True,
    )
    return viewer


def test_order_list_anonymous_redirects_to_login() -> None:
    response = Client().get(reverse("staff_order_list"))

    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]
    assert "next=" in response["Location"]


def test_order_list_without_capability_is_403() -> None:
    response = _client(staff(User.ROLE_DELIVERY)).get(reverse("staff_order_list"))

    assert response.status_code == 403


def test_order_list_shows_rows_for_packer() -> None:
    OrderFactory(order_number="BS/26-27/000901", ship_name="Asha Sharma")

    response = _client(staff(User.ROLE_PACKER)).get(reverse("staff_order_list"))
    html = response.content.decode()

    assert response.status_code == 200
    assert "BS/26-27/000901" in html
    assert "Asha Sharma" in html


def test_order_list_filter_and_search_work_from_the_query_string() -> None:
    wanted = OrderFactory(
        order_number="BS/26-27/000902",
        status=Order.Status.CONFIRMED,
    )
    other = OrderFactory(
        order_number="BS/26-27/000903",
        status=Order.Status.PACKING,
    )

    html = (
        _client(staff(User.ROLE_PACKER))
        .get(
            reverse("staff_order_list"),
            {"status": Order.Status.CONFIRMED, "q": "000902"},
        )
        .content.decode()
    )

    assert wanted.order_number in html
    assert other.order_number not in html


def test_order_list_shows_gate_reason() -> None:
    customer = UserFactory(phone_verified_at=None, email_verified_at=None)
    OrderFactory(user=customer, status=Order.Status.CONFIRMED)

    html = _client(staff(User.ROLE_PACKER)).get(reverse("staff_order_list")).content.decode()

    assert "phone number is not verified" in html


def test_pick_list_page_renders_for_packer() -> None:
    order = OrderFactory(
        order_number="BS/26-27/000904",
        status=Order.Status.CONFIRMED,
        promised_delivery_date=DELIVERY_DAY,
    )
    item = OrderItemFactory(order=order)

    response = _client(staff(User.ROLE_PACKER)).get(
        reverse("staff_pick_list"),
        {"day": DELIVERY_DAY.isoformat()},
    )

    assert response.status_code == 200
    assert item.product_name in response.content.decode()


def test_pick_list_has_print_stylesheet() -> None:
    html = _client(staff(User.ROLE_PACKER)).get(reverse("staff_pick_list")).content.decode()

    assert "@media print" in html


def test_order_detail_shows_status_log() -> None:
    order = OrderFactory(status=Order.Status.CONFIRMED)
    OrderStatusLog.objects.create(
        order=order,
        from_status=Order.Status.CONFIRMED,
        to_status=Order.Status.OUT_FOR_DELIVERY,
        note="Left the shop",
    )

    html = (
        _client(staff(User.ROLE_PACKER))
        .get(reverse("staff_order_detail", args=[order.order_number]))
        .content.decode()
    )

    assert "Out for delivery" in html
    assert "Left the shop" in html


def test_order_detail_hides_buttons_the_user_may_not_use() -> None:
    viewer = staff(User.ROLE_MEMBER)
    RoleCapability.objects.create(
        role=User.ROLE_MEMBER,
        capability=Capability.VIEW_ORDERS,
        allowed=True,
    )
    order = OrderFactory(status=Order.Status.CONFIRMED)

    html = (
        _client(viewer)
        .get(reverse("staff_order_detail", args=[order.order_number]))
        .content.decode()
    )

    assert order.order_number in html
    assert "Start packing" not in html
    assert "Cancel order" not in html


def test_start_packing_post_moves_order() -> None:
    order = OrderFactory(status=Order.Status.CONFIRMED)

    response = _client(staff(User.ROLE_PACKER)).post(
        reverse("staff_start_packing", args=[order.order_number])
    )

    assert response.status_code == 302
    order.refresh_from_db()
    assert order.status == Order.Status.PACKING


def test_send_out_post_moves_order_and_writes_sale() -> None:
    order, _row, _hold = order_with_hold()

    response = _client(staff(User.ROLE_PACKER)).post(
        reverse("staff_send_out", args=[order.order_number])
    )

    assert response.status_code == 302
    order.refresh_from_db()
    assert order.status == Order.Status.OUT_FOR_DELIVERY
    sales = InventoryMovement.objects.filter(
        movement_type=InventoryMovement.MovementType.SALE,
    )
    assert sales.count() == 1


def test_send_out_post_with_unverified_customer_shows_plain_message() -> None:
    customer = UserFactory(phone_verified_at=None, email_verified_at=None)
    order, _row, _hold = order_with_hold(customer=customer)

    response = _client(staff(User.ROLE_PACKER)).post(
        reverse("staff_send_out", args=[order.order_number])
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "phone number is not verified" in html
    order.refresh_from_db()
    assert order.status == Order.Status.PACKING


def test_send_out_post_with_short_stock_shows_plain_message() -> None:
    order, row, _hold = order_with_hold()
    # The hold exists, but the shelf no longer has what the order needs.
    adjust_stock(
        variant=row,
        quantity_change=Decimal("-9"),
        reason="Sold in the shop",
    )

    response = _client(staff(User.ROLE_PACKER)).post(
        reverse("staff_send_out", args=[order.order_number])
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "shelf count is lower" in html
    order.refresh_from_db()
    assert order.status == Order.Status.PACKING


def test_cancel_post_requires_reason() -> None:
    order = OrderFactory(status=Order.Status.CONFIRMED)

    response = _client(staff()).post(
        reverse("staff_cancel", args=[order.order_number]),
        {"reason": ""},
    )

    assert response.status_code == 200
    assert "needs a reason" in response.content.decode()
    order.refresh_from_db()
    assert order.status == Order.Status.CONFIRMED


def test_packing_slip_says_not_a_tax_invoice() -> None:
    order = OrderFactory(status=Order.Status.CONFIRMED)

    response = _client(staff(User.ROLE_PACKER)).get(
        reverse("staff_packing_slip", args=[order.order_number])
    )

    assert response.status_code == 200
    assert "Packing slip. Not a tax invoice." in response.content.decode()


def test_send_out_post_with_only_view_orders_is_403() -> None:
    order = OrderFactory(status=Order.Status.PACKING)

    response = _client(_view_orders_only()).post(
        reverse("staff_send_out", args=[order.order_number])
    )

    assert response.status_code == 403
    order.refresh_from_db()
    assert order.status == Order.Status.PACKING


def test_start_packing_post_with_only_view_orders_is_403() -> None:
    order = OrderFactory(status=Order.Status.CONFIRMED)

    response = _client(_view_orders_only()).post(
        reverse("staff_start_packing", args=[order.order_number])
    )

    assert response.status_code == 403
    order.refresh_from_db()
    assert order.status == Order.Status.CONFIRMED
