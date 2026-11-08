"""Tests for the phone login views (Slice 30, D59).

Every test uses the real URLs and the real session. A fake SMS backend records
the code, so the tests can post it back the way a customer would.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.views import CART_MERGE_MESSAGE
from apps.cart.models import Cart, CartItem
from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.tests.factories import InventoryMovementFactory
from apps.notifications.sms import SmsBackend

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
OTHER_PHONE = "+919876543211"

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


def _start(client: Client, phone: str = PHONE, next_url: str | None = None):
    """Post the phone the way the login form does."""
    data = {"phone": phone}
    if next_url is not None:
        data["next"] = next_url
    return client.post(reverse("account_login"), data)


def _code() -> str:
    return SENT[-1][1]


def _wrong_code() -> str:
    return f"{(int(_code()) + 1) % 1_000_000:06d}"


def _verify(client: Client, code: str):
    return client.post(reverse("account_verify"), {"code": code})


# ---------------------------------------------------------------------------
# The login page
# ---------------------------------------------------------------------------
def test_login_page_renders() -> None:
    response = Client().get(reverse("account_login"))

    assert response.status_code == 200
    assert 'name="phone"' in response.content.decode()


def test_a_valid_phone_sends_a_code_and_redirects_to_verify() -> None:
    client = Client()

    response = _start(client)

    assert response.status_code == 302
    assert response["Location"] == reverse("account_verify")
    assert len(SENT) == 1
    assert SENT[0][0] == PHONE
    assert client.session["otp_phone"] == PHONE


def test_a_bad_phone_shows_an_error_and_sends_nothing() -> None:
    client = Client()

    response = client.post(reverse("account_login"), {"phone": "12345"})

    assert response.status_code == 200
    assert "Enter a valid Indian mobile number." in response.content.decode()
    assert SENT == []


def test_known_and_unknown_phones_get_the_same_response() -> None:
    User.objects.create_user(phone=PHONE)

    known = _start(Client(), PHONE)
    unknown = _start(Client(), OTHER_PHONE)

    assert known.status_code == 302
    assert unknown.status_code == 302
    assert known["Location"] == unknown["Location"] == reverse("account_verify")
    assert len(SENT) == 2


# ---------------------------------------------------------------------------
# Logging in
# ---------------------------------------------------------------------------
def test_right_code_creates_a_member_and_logs_in() -> None:
    client = Client()
    _start(client)

    response = _verify(client, _code())

    assert response.status_code == 302
    assert response["Location"] == reverse("home")
    user = User.objects.get(phone=PHONE)
    assert user.role == User.ROLE_MEMBER
    assert user.is_staff is False
    assert user.is_superuser is False
    assert user.has_usable_password() is False
    assert client.session["_auth_user_id"] == str(user.pk)


def test_right_code_logs_in_an_existing_customer_without_creating_another() -> None:
    user = User.objects.create_user(phone=PHONE)
    client = Client()
    _start(client)

    response = _verify(client, _code())

    assert response.status_code == 302
    assert User.objects.filter(phone=PHONE).count() == 1
    assert client.session["_auth_user_id"] == str(user.pk)


def test_wrong_code_shows_a_generic_error_and_does_not_log_in() -> None:
    client = Client()
    _start(client)

    response = _verify(client, _wrong_code())

    assert response.status_code == 200
    assert "That code is not valid. Please try again." in response.content.decode()
    assert "_auth_user_id" not in client.session
    assert not User.objects.exists()


def test_verify_without_a_session_phone_redirects_to_login() -> None:
    response = Client().get(reverse("account_verify"))

    assert response.status_code == 302
    assert response["Location"] == reverse("account_login")


def test_a_staff_phone_logs_in_to_its_own_customer_account_by_otp() -> None:
    # A staff account and a customer account are separate rows (D72), so the
    # staff phone gets its own customer account and the staff row is untouched.
    staff = User.objects.create_user(phone=PHONE, password="ok", is_staff=True)
    client = Client()
    _start(client)

    response = _verify(client, _code())

    assert response.status_code == 302
    customer = User.objects.get(phone=PHONE, is_staff=False)
    assert customer.is_superuser is False
    assert client.session["_auth_user_id"] == str(customer.pk)
    staff.refresh_from_db()
    assert staff.has_usable_password() is True
    assert staff.phone_verified_at is None


def test_an_inactive_account_cannot_log_in_with_an_otp() -> None:
    inactive = User.objects.create_user(phone=PHONE)
    inactive.is_active = False
    inactive.save(update_fields=["is_active"])
    client = Client()
    _start(client)

    response = _verify(client, _code())

    assert response.status_code == 200
    assert "_auth_user_id" not in client.session
    assert User.objects.filter(phone=PHONE).count() == 1


# ---------------------------------------------------------------------------
# next, the session and logout
# ---------------------------------------------------------------------------
def test_next_on_the_same_host_is_followed() -> None:
    client = Client()
    _start(client, next_url="/products/")

    response = _verify(client, _code())

    assert response.status_code == 302
    assert response["Location"] == "/products/"


def test_next_on_another_host_is_ignored() -> None:
    client = Client()
    _start(client, next_url="https://evil.example/phish")

    response = _verify(client, _code())

    assert response.status_code == 302
    assert response["Location"] == reverse("home")


def test_login_changes_the_session_key() -> None:
    client = Client()
    _start(client)
    key_before = client.session.session_key
    assert key_before is not None

    _verify(client, _code())

    assert client.session.session_key is not None
    assert client.session.session_key != key_before


def test_logout_needs_post() -> None:
    user = User.objects.create_user(phone=PHONE)
    client = Client()
    client.force_login(user)

    response = client.get(reverse("account_logout"))

    assert response.status_code == 405


def test_logout_logs_the_user_out() -> None:
    user = User.objects.create_user(phone=PHONE)
    client = Client()
    client.force_login(user)

    response = client.post(reverse("account_logout"))

    assert response.status_code == 302
    assert response["Location"] == reverse("home")
    assert "_auth_user_id" not in client.session


def test_a_logged_in_user_is_redirected_from_login() -> None:
    user = User.objects.create_user(phone=PHONE)
    client = Client()
    client.force_login(user)

    response = client.get(reverse("account_login"))

    assert response.status_code == 302
    assert response["Location"] == reverse("home")


# ---------------------------------------------------------------------------
# Rate limiting, CSRF and the header
# ---------------------------------------------------------------------------
def test_a_rate_limited_request_shows_the_message_and_sends_nothing() -> None:
    client = Client()
    _start(client)

    response = client.post(reverse("account_login"), {"phone": PHONE})

    assert response.status_code == 200
    assert "Too many codes requested for this number. Please try again later." in (
        response.content.decode()
    )
    assert len(SENT) == 1


def test_csrf_is_enforced_on_the_login_post() -> None:
    client = Client(enforce_csrf_checks=True)

    response = client.post(reverse("account_login"), {"phone": PHONE})

    assert response.status_code == 403
    assert SENT == []


def test_header_shows_login_when_logged_out_and_logout_when_logged_in() -> None:
    client = Client()

    page = client.get(reverse("home")).content.decode()
    assert 'href="/account/login/"' in page
    assert 'action="/account/logout/"' not in page

    user = User.objects.create_user(phone=PHONE)
    client.force_login(user)
    page = client.get(reverse("home")).content.decode()
    assert 'action="/account/logout/"' in page
    assert 'href="/account/login/"' not in page


# ---------------------------------------------------------------------------
# The guest cart on login (Slice 31, D60)
# ---------------------------------------------------------------------------
def _variant_with_stock(quantity: str = "10.000"):
    variant = ProductVariantFactory(is_active=True)
    InventoryMovementFactory(variant=variant, quantity_change=Decimal(quantity))
    return variant


def _put_guest_cart(client: Client, variant_id: int, quantity: str) -> None:
    session = client.session
    session["guest_cart"] = {str(variant_id): quantity}
    session.save()


def test_otp_phone_session_key_is_gone_after_login() -> None:
    client = Client()
    _start(client)

    _verify(client, _code())

    assert "otp_phone" not in client.session


def test_guest_cart_is_moved_into_the_saved_cart_on_login() -> None:
    variant = _variant_with_stock()
    client = Client()
    _start(client)
    _put_guest_cart(client, variant.pk, "2.000")

    _verify(client, _code())

    user = User.objects.get(phone=PHONE)
    item = CartItem.objects.get(cart__user=user)
    assert item.variant_id == variant.pk
    assert item.quantity == Decimal("2.000")


def test_guest_cart_session_key_is_gone_after_login() -> None:
    variant = _variant_with_stock()
    client = Client()
    _start(client)
    _put_guest_cart(client, variant.pk, "1.000")

    _verify(client, _code())

    assert "guest_cart" not in client.session


def test_login_without_a_guest_cart_creates_no_cart() -> None:
    client = Client()
    _start(client)

    _verify(client, _code())

    assert Cart.objects.count() == 0


def test_login_shows_a_message_when_the_merge_changed_a_line() -> None:
    variant = _variant_with_stock("1.000")
    client = Client()
    _start(client)
    _put_guest_cart(client, variant.pk, "2.000")

    response = _verify(client, _code())

    # The redirect target does not render messages, so read the storage this
    # request queued them in.
    stored = [str(message) for message in get_messages(response.wsgi_request)]
    assert str(CART_MERGE_MESSAGE) in stored


def test_a_failed_code_keeps_the_guest_cart_in_the_session() -> None:
    variant = _variant_with_stock()
    client = Client()
    _start(client)
    _put_guest_cart(client, variant.pk, "3.000")

    _verify(client, _wrong_code())

    assert client.session["guest_cart"] == {str(variant.pk): "3.000"}
    assert not User.objects.exists()
