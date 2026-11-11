"""The staff capability matrix and every staff route (D75, D79 amendment)."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.capabilities import Capability
from apps.accounts.models import RoleCapability, User
from apps.orders.models import Order
from apps.orders.tests.factories import OrderFactory
from apps.orders.tests.staff_helpers import order_with_hold, staff

pytestmark = pytest.mark.django_db

# The pages a signed-in staff member can open with a GET.
GET_URLS = (
    "staff_home",
    "staff_order_list",
    "staff_pick_list",
    "staff_delivery_list",
    "staff_cash",
    "staff_credit_dues",
)


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def test_every_staff_url_requires_login() -> None:
    for name in GET_URLS:
        response = Client().get(reverse(name))

        assert response.status_code == 302, name
        assert reverse("account_login") in response["Location"], name


def test_every_staff_post_checks_its_capability() -> None:
    confirmed = OrderFactory(status=Order.Status.CONFIRMED)
    packing, _row, _hold = order_with_hold()
    delivery = _client(staff(User.ROLE_DELIVERY))
    packer = _client(staff(User.ROLE_PACKER))

    assert (
        delivery.post(reverse("staff_start_packing", args=[confirmed.order_number])).status_code
        == 403
    )
    assert (
        packer.post(
            reverse("staff_mark_delivered", args=[packing.order_number]),
            {"collected_amount": "230.00"},
        ).status_code
        == 403
    )
    assert (
        packer.post(
            reverse("staff_cancel", args=[confirmed.order_number]),
            {"reason": "Out of stock"},
        ).status_code
        == 403
    )
    assert (
        packer.post(reverse("staff_send_refund", args=[confirmed.order_number])).status_code == 403
    )

    confirmed.refresh_from_db()
    packing.refresh_from_db()
    assert confirmed.status == Order.Status.CONFIRMED
    assert packing.status == Order.Status.PACKING


def test_matrix_override_removes_access_immediately() -> None:
    packer = staff(User.ROLE_PACKER)
    row = RoleCapability.objects.create(
        role=User.ROLE_PACKER,
        capability=Capability.VIEW_ORDERS,
        allowed=False,
    )
    client = _client(packer)

    assert client.get(reverse("staff_order_list")).status_code == 403

    row.delete()

    # No cache is kept, so the next check reads the default again.
    assert client.get(reverse("staff_order_list")).status_code == 200


def test_matrix_override_grants_access_immediately() -> None:
    client = _client(staff(User.ROLE_DELIVERY))

    assert client.get(reverse("staff_order_list")).status_code == 403

    RoleCapability.objects.create(
        role=User.ROLE_DELIVERY,
        capability=Capability.VIEW_ORDERS,
        allowed=True,
    )

    assert client.get(reverse("staff_order_list")).status_code == 200


def test_customer_without_staff_flag_is_403_everywhere() -> None:
    customer = _client(User.objects.create_user(phone="+919000000099"))

    for name in ("staff_order_list", "staff_delivery_list", "staff_credit_dues"):
        assert customer.get(reverse(name)).status_code == 403, name


def test_403_page_is_plain_words() -> None:
    response = _client(staff(User.ROLE_DELIVERY)).get(reverse("staff_order_list"))
    html = response.content.decode()

    assert response.status_code == 403
    assert "permission" in html
    assert "Traceback" not in html


def test_delivery_role_defaults_match_the_screens() -> None:
    order = OrderFactory(status=Order.Status.OUT_FOR_DELIVERY)
    client = _client(staff(User.ROLE_DELIVERY))

    assert client.get(reverse("staff_delivery_list")).status_code == 200
    assert client.get(reverse("staff_cash")).status_code == 200
    assert client.get(reverse("staff_order_list")).status_code == 403
    assert client.get(reverse("staff_pick_list")).status_code == 403
    assert client.get(reverse("staff_order_detail", args=[order.order_number])).status_code == 403
    assert client.get(reverse("staff_credit_dues")).status_code == 403


def test_packer_defaults_match_the_screens() -> None:
    order = OrderFactory(status=Order.Status.CONFIRMED)
    client = _client(staff(User.ROLE_PACKER))

    assert client.get(reverse("staff_order_list")).status_code == 200
    assert client.get(reverse("staff_pick_list")).status_code == 200
    assert client.get(reverse("staff_order_detail", args=[order.order_number])).status_code == 200
    assert client.get(reverse("staff_delivery_list")).status_code == 403
    assert client.get(reverse("staff_cash")).status_code == 403
    assert client.get(reverse("staff_credit_dues")).status_code == 403
