"""End-to-end shop journeys over the real pages (D73).

Every test walks the pages a customer and the shop really use: the cart, the
sign-up and login forms, the verification pages, checkout, the order pages and
the cancel page. Only the setup (an address, stock, a delivery pin code) uses
the small helpers.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.cart.models import CartItem
from apps.credit.services import set_credit_limit
from apps.inventory.availability import get_available_quantity
from apps.notifications.tests.recording import RecordingSmsBackend
from apps.orders.models import Order
from apps.orders.presentation import STATUS_SENTENCES
from apps.orders.services import (
    CustomerNotVerifiedError,
    transition_order,
)
from apps.orders.state import DELIVERED, OUT_FOR_DELIVERY, PACKING
from apps.orders.tests.checkout_helpers import (
    NOW,
    address,
    clear_settings,
    fill_cart,
    open_checkout,
    place_cod,
    post_data,
    reset_settings,
    serve,
    variant,
)
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
EMAIL = "asha@example.com"
PASSWORD = "StrongPass1"
OLD_PASSWORD = "OldPass123"
NEW_PASSWORD = "NewPass456"
STAFF_PASSWORD = "StaffPass1"
SQUATTER_PASSWORD = "Squatter1"


@pytest.fixture(autouse=True)
def _ready(settings):
    """One settings row, a recording SMS backend and an empty cache per test."""
    reset_settings()
    settings.SMS_BACKEND = "apps.notifications.tests.recording.RecordingSmsBackend"
    RecordingSmsBackend.codes.clear()
    yield
    RecordingSmsBackend.codes.clear()
    clear_settings()


def _signup_data(*, password: str = PASSWORD) -> dict[str, str]:
    return {
        "phone": "98765 43210",
        "full_name": "Asha Sharma",
        "email": EMAIL,
        "password1": password,
        "password2": password,
    }


def _phone_code() -> str:
    return RecordingSmsBackend.codes[-1][1]


def _email_code(mailoutbox) -> str:
    match = re.search(r"\b(\d{6})\b", mailoutbox[-1].body)
    assert match is not None
    return match.group(1)


def _cancel(client: Client, order: Order):
    return client.post(
        reverse("order_cancel", args=[order.checkout_group]),
        {"reason": "Changed my mind"},
        follow=True,
    )


def _detail(client: Client, order: Order) -> str:
    return client.get(reverse("order_detail", args=[order.checkout_group])).content.decode()


def test_guest_signs_up_shops_and_cancels_a_cod_order() -> None:
    row = variant()
    serve()
    guest = Client()

    guest.post(reverse("cart_add"), {"variant_id": row.pk, "quantity": "2"})
    guest.post(reverse("account_signup"), _signup_data())

    user = User.objects.get(phone=PHONE)
    assert CartItem.objects.filter(cart__user=user).exists()

    address_row = address(user)
    group, token = open_checkout(guest)
    response = guest.post(
        reverse("checkout"),
        post_data(group, token, address_row),
    )

    order = Order.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("order_confirmation", args=[order.checkout_group])
    assert order.order_number in guest.get(response["Location"]).content.decode()

    orders_html = guest.get(reverse("order_list")).content.decode()
    assert order.order_number in orders_html
    assert order.order_number in _detail(guest, order)

    _cancel(guest, order)

    order.refresh_from_db()
    cancelled = str(STATUS_SENTENCES[Order.Status.CANCELLED])
    assert order.status == Order.Status.CANCELLED
    assert cancelled in _detail(guest, order)
    assert cancelled in guest.get(reverse("order_list")).content.decode()
    # The reservation was released, so the two packets are orderable again.
    assert get_available_quantity(row) == Decimal("10.000")


def test_otp_customer_places_a_credit_order_and_the_cancel_returns_the_credit() -> None:
    user = User.objects.create_user(phone=PHONE)
    staff = UserFactory(is_staff=True)
    account = set_credit_limit(user, Decimal("1000.00"), actor=staff)
    serve()
    address_row = address(user)

    client = Client()
    client.post(reverse("account_login"), {"method": "otp", "phone": PHONE})
    response = client.post(reverse("account_verify"), {"code": _phone_code()})
    assert response.status_code == 302

    row = variant()
    client.post(reverse("cart_add"), {"variant_id": row.pk, "quantity": "2"})
    group, token = open_checkout(client)
    client.post(
        reverse("checkout"),
        post_data(group, token, address_row, use_credit=True),
    )

    order = Order.objects.get()
    account.refresh_from_db()
    assert account.balance == Decimal("230.00")

    _cancel(client, order)

    order.refresh_from_db()
    account.refresh_from_db()
    assert order.status == Order.Status.CANCELLED
    assert account.balance == Decimal("0.00")


def test_signup_customer_cannot_be_sent_out_until_phone_and_email_are_verified_through_the_pages(
    settings,
    django_capture_on_commit_callbacks,
    mailoutbox,
) -> None:
    user = User.objects.create_user(
        phone=PHONE,
        password=PASSWORD,
        email=EMAIL,
        full_name="Asha Sharma",
    )
    serve()
    address_row = address(user)
    fill_cart(user, variant())
    order = place_cod(user, address_row).order
    staff = UserFactory(is_staff=True)
    transition_order(order.pk, PACKING, actor=staff, now=NOW)

    with pytest.raises(CustomerNotVerifiedError):
        transition_order(order.pk, OUT_FOR_DELIVERY, actor=staff, now=NOW)

    client = Client()
    client.force_login(user)

    client.post(reverse("account_verify_phone"), {"action": "send"})
    client.post(
        reverse("account_verify_phone"),
        {"action": "confirm", "code": _phone_code()},
    )
    with pytest.raises(CustomerNotVerifiedError):
        transition_order(order.pk, OUT_FOR_DELIVERY, actor=staff, now=NOW)

    client.post(reverse("account_verify_email"), {"action": "send"})
    client.post(
        reverse("account_verify_email"),
        {"action": "confirm", "code": _email_code(mailoutbox)},
    )

    settings.ORDER_EMAIL_ENABLED = True
    with django_capture_on_commit_callbacks(execute=True):
        transition_order(order.pk, OUT_FOR_DELIVERY, actor=staff, now=NOW)
    with django_capture_on_commit_callbacks(execute=True):
        transition_order(order.pk, DELIVERED, actor=staff, now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.DELIVERED
    subjects = [message.subject for message in mailoutbox]
    assert any("out for delivery" in subject for subject in subjects)
    assert any("delivered" in subject for subject in subjects)


def test_guest_cart_survives_a_password_login() -> None:
    row = variant()
    user = User.objects.create_user(phone=PHONE, password=PASSWORD, email=EMAIL)
    guest = Client()

    guest.post(reverse("cart_add"), {"variant_id": row.pk, "quantity": "2"})
    response = guest.post(
        reverse("account_login"),
        {"method": "password", "identifier": PHONE, "password": PASSWORD},
    )

    assert response.status_code == 302
    assert guest.session["_auth_user_id"] == str(user.pk)
    item = CartItem.objects.get(cart__user=user)
    assert item.quantity == Decimal("2.000")


def test_forgot_password_by_phone_then_login_with_the_new_password() -> None:
    user = User.objects.create_user(phone=PHONE, password=OLD_PASSWORD, email=EMAIL)
    client = Client()

    client.post(reverse("account_forgot_password"), {"identifier": PHONE})
    reset = client.post(
        reverse("account_reset_password"),
        {
            "code": _phone_code(),
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        },
    )

    assert reset.status_code == 302
    assert reset["Location"] == reverse("account_login")
    login = client.post(
        reverse("account_login"),
        {"method": "password", "identifier": PHONE, "password": NEW_PASSWORD},
    )
    assert login.status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)


def test_forgot_password_by_email_then_login_with_the_new_password(mailoutbox) -> None:
    user = User.objects.create_user(phone=PHONE, password=OLD_PASSWORD, email=EMAIL)
    client = Client()

    client.post(reverse("account_forgot_password"), {"identifier": EMAIL})
    reset = client.post(
        reverse("account_reset_password"),
        {
            "code": _email_code(mailoutbox),
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        },
    )

    assert reset.status_code == 302
    login = client.post(
        reverse("account_login"),
        {"method": "password", "identifier": PHONE, "password": NEW_PASSWORD},
    )
    assert login.status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)


def test_otp_login_takes_over_a_squatted_account_end_to_end() -> None:
    squatter = Client()
    squatter.post(reverse("account_signup"), _signup_data(password=SQUATTER_PASSWORD))
    user = User.objects.get(phone=PHONE)
    assert user.email == EMAIL
    assert user.has_usable_password() is True

    owner = Client()
    owner.post(reverse("account_login"), {"method": "otp", "phone": PHONE})
    response = owner.post(reverse("account_verify"), {"code": _phone_code()})

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.phone_verified_at is not None
    assert user.email is None
    assert user.email_verified_at is None
    assert user.has_usable_password() is False
    assert user.is_ready_for_delivery is True

    # The squatter's own session is still logged in, so log it out before
    # trying the old password; the login page would otherwise just redirect.
    squatter.logout()
    refused = squatter.post(
        reverse("account_login"),
        {"method": "password", "identifier": PHONE, "password": SQUATTER_PASSWORD},
    )
    assert refused.status_code == 200
    assert "_auth_user_id" not in squatter.session


def test_staff_and_customer_accounts_with_one_phone_work_side_by_side() -> None:
    customer = User.objects.create_user(
        phone=PHONE,
        password=PASSWORD,
        email=EMAIL,
        full_name="Asha Sharma",
    )
    staff = User.objects.create_user(
        phone=PHONE,
        password=STAFF_PASSWORD,
        email=EMAIL,
        is_staff=True,
    )
    serve()
    address_row = address(customer)
    fill_cart(customer, variant())
    order = place_cod(customer, address_row).order

    admin_client = Client()
    allowed = admin_client.post(
        reverse("admin:login"),
        {"username": PHONE, "password": STAFF_PASSWORD, "next": "/admin/"},
    )
    assert allowed.status_code == 302
    assert admin_client.session["_auth_user_id"] == str(staff.pk)
    assert admin_client.get("/admin/").status_code == 200

    shop_client = Client()
    response = shop_client.post(
        reverse("account_login"),
        {"method": "password", "identifier": PHONE, "password": PASSWORD},
    )
    assert response.status_code == 302
    assert shop_client.session["_auth_user_id"] == str(customer.pk)

    # Neither password opens the other account space.
    wrong_shop = Client()
    refused_shop = wrong_shop.post(
        reverse("account_login"),
        {"method": "password", "identifier": PHONE, "password": STAFF_PASSWORD},
    )
    assert refused_shop.status_code == 200
    assert "_auth_user_id" not in wrong_shop.session

    wrong_admin = Client()
    refused_admin = wrong_admin.post(
        reverse("admin:login"),
        {"username": PHONE, "password": PASSWORD, "next": "/admin/"},
    )
    assert refused_admin.status_code == 200
    assert "_auth_user_id" not in wrong_admin.session

    # Each session sees only its own space.
    assert order.order_number not in admin_client.get(reverse("order_list")).content.decode()
    assert shop_client.get(reverse("admin:index")).status_code == 302
