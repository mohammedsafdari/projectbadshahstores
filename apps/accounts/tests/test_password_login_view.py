"""The login page with both the password form and the code form (D72)."""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.accounts.auth_presentation import (
    LOGIN_ERROR_MESSAGE,
    LOGIN_LOCKED_MESSAGE,
)
from apps.accounts.models import User
from apps.cart.models import CartItem
from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.tests.factories import InventoryMovementFactory
from apps.notifications.sms import SmsBackend

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
EMAIL = "asha@example.com"
PASSWORD = "StrongPass1"

SENT: list[tuple[str, str]] = []


class RecordingSmsBackend(SmsBackend):
    """Keep the codes in memory instead of printing or sending them."""

    def send_otp(self, phone: str, code: str) -> None:
        SENT.append((phone, code))


@pytest.fixture(autouse=True)
def _recording_sms(settings):
    """Point SMS_BACKEND at the fake and empty the list around every test."""
    settings.SMS_BACKEND = f"{__name__}.RecordingSmsBackend"
    SENT.clear()
    yield
    SENT.clear()


def _customer() -> User:
    return User.objects.create_user(phone=PHONE, password=PASSWORD, email=EMAIL)


@pytest.fixture(autouse=True)
def _clear_cache():
    """The login throttle lives in the cache, so no test inherits a lock."""
    cache.clear()
    yield
    cache.clear()


def _login(client: Client, identifier: str = PHONE, password: str = PASSWORD, **extra):
    # ``**extra`` collects extra keyword arguments into a dict, so a test can
    # add one form field (such as "next") without repeating the whole payload.
    data = {"method": "password", "identifier": identifier, "password": password}
    data.update(extra)
    return client.post(reverse("account_login"), data)


def _variant_with_stock(quantity: str = "10.000"):
    variant = ProductVariantFactory(is_active=True)
    InventoryMovementFactory(variant=variant, quantity_change=Decimal(quantity))
    return variant


def _put_guest_cart(client: Client, variant_id: int, quantity: str) -> None:
    session = client.session
    session["guest_cart"] = {str(variant_id): quantity}
    session.save()


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------
def test_login_page_shows_both_forms() -> None:
    html = Client().get(reverse("account_login")).content.decode()

    assert "data-password-login-form" in html
    assert "data-otp-login-form" in html


def test_login_page_links_to_signup() -> None:
    html = Client().get(reverse("account_login")).content.decode()

    assert "data-signup-link" in html
    assert f'href="{reverse("account_signup")}"' in html


# ---------------------------------------------------------------------------
# Logging in
# ---------------------------------------------------------------------------
def test_password_login_with_a_phone_logs_in_and_goes_home() -> None:
    user = _customer()
    client = Client()

    response = _login(client)

    assert response.status_code == 302
    assert response["Location"] == reverse("home")
    assert client.session["_auth_user_id"] == str(user.pk)


def test_password_login_with_an_email_logs_in() -> None:
    _customer()
    client = Client()

    response = _login(client, identifier=EMAIL)

    assert response.status_code == 302
    assert "_auth_user_id" in client.session


def test_password_login_follows_a_safe_next() -> None:
    _customer()

    response = _login(Client(), next="/cart/")

    assert response.status_code == 302
    assert response["Location"] == "/cart/"


def test_password_login_ignores_an_unsafe_next() -> None:
    _customer()

    response = _login(Client(), next="https://evil.example/phish")

    assert response.status_code == 302
    assert response["Location"] == reverse("home")


def test_password_login_keeps_the_guest_cart() -> None:
    _customer()
    variant = _variant_with_stock()
    client = Client()
    _put_guest_cart(client, variant.pk, "2.000")

    _login(client)

    item = CartItem.objects.get(cart__user__phone=PHONE)
    assert item.variant_id == variant.pk
    assert item.quantity == Decimal("2.000")


def test_wrong_password_shows_one_generic_error() -> None:
    _customer()
    client = Client()

    response = _login(client, password="WrongPass9")

    html = response.content.decode()
    assert response.status_code == 200
    assert str(LOGIN_ERROR_MESSAGE) in html
    assert html.count("data-login-error") == 1
    assert "_auth_user_id" not in client.session


def test_unknown_account_shows_the_same_error() -> None:
    html = _login(Client(), identifier="+919999999999").content.decode()

    assert str(LOGIN_ERROR_MESSAGE) in html
    assert str(LOGIN_LOCKED_MESSAGE) not in html


def test_locked_account_shows_the_lock_message() -> None:
    _customer()
    client = Client()
    for _attempt in range(5):
        _login(client, password="WrongPass9")

    response = _login(client)

    assert response.status_code == 200
    assert str(LOGIN_LOCKED_MESSAGE) in response.content.decode()
    assert "_auth_user_id" not in client.session


def test_staff_password_does_not_log_in_on_the_shop() -> None:
    User.objects.create_user(phone=PHONE, password="StaffPass1", is_staff=True)
    client = Client()

    response = _login(client, password="StaffPass1")

    assert response.status_code == 200
    assert str(LOGIN_ERROR_MESSAGE) in response.content.decode()
    assert "_auth_user_id" not in client.session


def test_unverified_customer_can_log_in() -> None:
    _customer()
    client = Client()

    response = _login(client)

    assert response.status_code == 302
    assert "_auth_user_id" in client.session


def test_password_login_does_not_change_the_verified_times() -> None:
    user = _customer()
    user.phone_verified_at = user.created_at
    user.email_verified_at = user.created_at
    user.save(update_fields=["phone_verified_at", "email_verified_at"])

    _login(Client())

    user.refresh_from_db()
    assert user.phone_verified_at == user.created_at
    assert user.email_verified_at == user.created_at


# ---------------------------------------------------------------------------
# The code form still behaves as before
# ---------------------------------------------------------------------------
def test_a_post_without_a_method_still_sends_an_otp_code() -> None:
    client = Client()

    response = client.post(reverse("account_login"), {"phone": PHONE})

    assert response.status_code == 302
    assert response["Location"] == reverse("account_verify")
    assert len(SENT) == 1


def test_a_post_with_method_otp_sends_an_otp_code() -> None:
    client = Client()

    response = client.post(
        reverse("account_login"),
        {"method": "otp", "phone": PHONE},
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("account_verify")
    assert len(SENT) == 1


# ---------------------------------------------------------------------------
# Guards and the session
# ---------------------------------------------------------------------------
def test_password_form_needs_a_csrf_token() -> None:
    _customer()
    client = Client(enforce_csrf_checks=True)

    response = _login(client)

    assert response.status_code == 403
    assert "_auth_user_id" not in client.session


def test_logged_in_customer_is_redirected_from_login() -> None:
    customer = _customer()
    client = Client()
    client.force_login(customer)

    response = client.get(reverse("account_login"))

    assert response.status_code == 302
    assert response["Location"] == reverse("home")


def test_password_login_starts_a_new_session_key() -> None:
    _customer()
    client = Client()
    session = client.session
    session["marker"] = "before"
    session.save()
    key_before = session.session_key

    _login(client)

    assert client.session.session_key != key_before


def test_failed_login_never_logs_the_password(caplog) -> None:
    _customer()
    caplog.set_level(logging.DEBUG)

    _login(Client(), password="Uniqu3SecretText")

    assert "Uniqu3SecretText" not in caplog.text
