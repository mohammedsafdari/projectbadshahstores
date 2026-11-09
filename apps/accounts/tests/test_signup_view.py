"""The sign-up page: creation, login, errors and the guest cart (D72)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse

from apps.accounts.auth_presentation import (
    PASSWORDS_DIFFER_MESSAGE,
    SIGNUP_DONE_MESSAGE,
    SIGNUP_EXISTS_MESSAGE,
)
from apps.accounts.models import User
from apps.cart.models import CartItem
from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.tests.factories import InventoryMovementFactory

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
EMAIL = "asha@example.com"
PASSWORD = "StrongPass1"


def _data(**overrides) -> dict[str, str]:
    # ``**overrides`` collects extra keyword arguments into a dict, so one test
    # can change a single field and leave the rest of the form data alone.
    data = {
        "phone": "98765 43210",
        "full_name": "Asha Rao",
        "email": EMAIL,
        "password1": PASSWORD,
        "password2": PASSWORD,
    }
    data.update(overrides)
    return data


def _variant_with_stock(quantity: str = "10.000"):
    variant = ProductVariantFactory(is_active=True)
    InventoryMovementFactory(variant=variant, quantity_change=Decimal(quantity))
    return variant


def _put_guest_cart(client: Client, variant_id: int, quantity: str) -> None:
    session = client.session
    session["guest_cart"] = {str(variant_id): quantity}
    session.save()


def test_signup_page_shows_the_form() -> None:
    response = Client().get(reverse("account_signup"))

    assert response.status_code == 200
    assert "data-signup-form" in response.content.decode()


def test_signup_creates_the_account_and_logs_in() -> None:
    client = Client()

    response = client.post(reverse("account_signup"), _data())

    assert response.status_code == 302
    assert response["Location"] == reverse("home")
    user = User.objects.get(phone=PHONE)
    assert client.session["_auth_user_id"] == str(user.pk)


def test_signup_redirects_to_a_safe_next() -> None:
    client = Client()

    response = client.post(reverse("account_signup"), _data(next="/cart/"))

    assert response.status_code == 302
    assert response["Location"] == "/cart/"


def test_signup_ignores_an_unsafe_next() -> None:
    client = Client()

    response = client.post(
        reverse("account_signup"),
        _data(next="https://evil.example/phish"),
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("home")


def test_signup_shows_the_verification_message() -> None:
    client = Client()

    response = client.post(reverse("account_signup"), _data())

    stored = [str(message) for message in get_messages(response.wsgi_request)]
    assert str(SIGNUP_DONE_MESSAGE) in stored


def test_signup_keeps_the_guest_cart() -> None:
    variant = _variant_with_stock()
    client = Client()
    _put_guest_cart(client, variant.pk, "2.000")

    client.post(reverse("account_signup"), _data())

    user = User.objects.get(phone=PHONE)
    item = CartItem.objects.get(cart__user=user)
    assert item.variant_id == variant.pk
    assert item.quantity == Decimal("2.000")


def test_signup_with_an_existing_phone_shows_one_generic_error_and_creates_nothing() -> None:
    User.objects.create_user(phone=PHONE, password="OtherPass1")
    client = Client()

    response = client.post(reverse("account_signup"), _data(email="other@example.com"))

    html = response.content.decode()
    assert response.status_code == 200
    assert html.count(str(SIGNUP_EXISTS_MESSAGE)) == 1
    assert User.objects.filter(phone=PHONE).count() == 1
    assert "_auth_user_id" not in client.session


def test_signup_with_an_existing_email_shows_the_same_generic_error() -> None:
    User.objects.create_user(phone="+919876543299", password="OtherPass1", email=EMAIL)
    client = Client()

    response = client.post(reverse("account_signup"), _data(phone="9876543298"))

    html = response.content.decode()
    assert response.status_code == 200
    assert html.count(str(SIGNUP_EXISTS_MESSAGE)) == 1
    assert User.objects.filter(email=EMAIL).count() == 1
    assert "_auth_user_id" not in client.session


def test_signup_with_different_passwords_shows_an_error_and_creates_nothing() -> None:
    response = Client().post(
        reverse("account_signup"),
        _data(password1=PASSWORD, password2="AnotherPass1"),
    )

    assert response.status_code == 200
    assert str(PASSWORDS_DIFFER_MESSAGE) in response.content.decode()
    assert not User.objects.exists()


def test_signup_with_a_weak_password_shows_the_validator_message() -> None:
    response = Client().post(
        reverse("account_signup"),
        _data(password1="12345678", password2="12345678"),
    )

    assert response.status_code == 200
    assert "entirely numeric" in response.content.decode()
    assert not User.objects.exists()


def test_signup_for_a_staff_phone_creates_a_separate_customer_account() -> None:
    staff = User.objects.create_user(phone=PHONE, password="StaffPass1", is_staff=True)
    client = Client()

    response = client.post(reverse("account_signup"), _data(email="other@example.com"))

    assert response.status_code == 302
    assert User.objects.filter(phone=PHONE).count() == 2
    customer = User.objects.get(phone=PHONE, is_staff=False)
    assert customer.pk != staff.pk
    assert client.session["_auth_user_id"] == str(customer.pk)


def test_signup_does_not_store_the_password_in_plain_text() -> None:
    Client().post(reverse("account_signup"), _data())

    user = User.objects.get(phone=PHONE)
    assert user.password != PASSWORD
    assert user.check_password(PASSWORD) is True


def test_signup_needs_a_csrf_token() -> None:
    client = Client(enforce_csrf_checks=True)

    response = client.post(reverse("account_signup"), _data())

    assert response.status_code == 403
    assert not User.objects.exists()


def test_signup_rejects_put() -> None:
    response = Client().put(reverse("account_signup"), _data())

    assert response.status_code == 405
    assert not User.objects.exists()


def test_signup_page_is_not_cached() -> None:
    response = Client().get(reverse("account_signup"))

    assert "no-store" in response["Cache-Control"]


def test_logged_in_customer_is_redirected_from_signup() -> None:
    customer = User.objects.create_user(phone=PHONE, password=PASSWORD)
    client = Client()
    client.force_login(customer)

    response = client.get(reverse("account_signup"))

    assert response.status_code == 302
    assert response["Location"] == reverse("home")


def test_signup_page_links_to_login() -> None:
    html = Client().get(reverse("account_signup")).content.decode()

    assert "data-login-link" in html
    assert f'href="{reverse("account_login")}"' in html


def test_new_account_is_a_member_without_staff_flags() -> None:
    Client().post(reverse("account_signup"), _data())

    user = User.objects.get(phone=PHONE)
    assert user.role == User.ROLE_MEMBER
    assert user.is_staff is False
    assert user.is_superuser is False


def test_signup_exists_message_is_a_form_error_not_a_field_error() -> None:
    User.objects.create_user(phone=PHONE, password="OtherPass1")
    client = Client()

    response = client.post(reverse("account_signup"), _data(email="other@example.com"))

    form = response.context["form"]
    # ``__all__`` is Django's key for a non-field (whole-form) error.
    assert list(form.errors) == ["__all__"]
    assert [str(error) for error in form.non_field_errors()] == [str(SIGNUP_EXISTS_MESSAGE)]
    html = response.content.decode()
    assert html.count(str(SIGNUP_EXISTS_MESSAGE)) == 1
    assert "data-form-error" in html
    assert "data-field-error" not in html
