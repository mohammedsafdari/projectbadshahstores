"""Separate staff and customer accounts (Slice 38f, D72).

One phone and one email may belong to one staff account and one customer
account. The two rows have different passwords and never share rights.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.notifications.sms import SmsBackend

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
OTHER_PHONE = "+919876543211"
EMAIL = "shared@example.com"

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


def _customer(phone: str = PHONE, email: str | None = None) -> User:
    return User.objects.create_user(phone=phone, password="CustomerPass1", email=email)


def _staff(phone: str = PHONE, email: str | None = None) -> User:
    return User.objects.create_user(
        phone=phone,
        password="StaffPass1",
        email=email,
        is_staff=True,
    )


def _start_otp(client: Client, phone: str = PHONE):
    return client.post(reverse("account_login"), {"phone": phone})


def _code() -> str:
    return SENT[-1][1]


def _verify(client: Client, code: str):
    return client.post(reverse("account_verify"), {"code": code})


# ---------------------------------------------------------------------------
# The two account spaces
# ---------------------------------------------------------------------------
def test_a_staff_and_a_customer_account_may_share_a_phone() -> None:
    customer = _customer()
    staff = _staff()

    assert customer.pk != staff.pk
    assert User.objects.filter(phone=PHONE, is_staff=False).count() == 1
    assert User.objects.filter(phone=PHONE, is_staff=True).count() == 1


def test_a_staff_and_a_customer_account_may_share_an_email() -> None:
    customer = _customer(email=EMAIL)
    staff = _staff(phone=OTHER_PHONE, email=EMAIL)

    assert customer.email == staff.email == EMAIL
    assert User.objects.filter(email=EMAIL).count() == 2


def test_two_active_customer_accounts_cannot_share_a_phone() -> None:
    _customer()
    # Two context managers on one line: the atomic block must be rolled back
    # after the expected database error, or the connection stays unusable.
    with transaction.atomic(), pytest.raises(IntegrityError):
        _customer(email="other@example.com")


def test_two_active_staff_accounts_cannot_share_a_phone() -> None:
    _staff()
    with transaction.atomic(), pytest.raises(IntegrityError):
        _staff(email="other@example.com")


def test_two_active_customer_accounts_cannot_share_an_email() -> None:
    _customer(email=EMAIL)
    with transaction.atomic(), pytest.raises(IntegrityError):
        _customer(phone=OTHER_PHONE, email=EMAIL)


def test_two_active_staff_accounts_cannot_share_an_email() -> None:
    _staff(email=EMAIL)
    with transaction.atomic(), pytest.raises(IntegrityError):
        _staff(phone=OTHER_PHONE, email=EMAIL)


# ---------------------------------------------------------------------------
# The natural key and the admin login
# ---------------------------------------------------------------------------
def test_natural_key_finds_only_the_staff_account() -> None:
    _customer()
    staff = _staff()

    found = User.objects.get_by_natural_key(PHONE)

    assert found.pk == staff.pk
    assert found.is_staff is True


def test_natural_key_does_not_find_a_customer_only_phone() -> None:
    _customer()

    with pytest.raises(User.DoesNotExist):
        User.objects.get_by_natural_key(PHONE)


def test_admin_login_uses_the_staff_password_not_the_customer_password() -> None:
    _customer()
    staff = _staff()
    client = Client()

    refused = client.post(
        reverse("admin:login"),
        {"username": PHONE, "password": "CustomerPass1", "next": "/admin/"},
    )
    assert refused.status_code == 200
    assert "_auth_user_id" not in client.session

    allowed = client.post(
        reverse("admin:login"),
        {"username": PHONE, "password": "StaffPass1", "next": "/admin/"},
    )
    assert allowed.status_code == 302
    assert client.session["_auth_user_id"] == str(staff.pk)


def test_customer_password_does_not_open_the_admin() -> None:
    customer = _customer()
    client = Client()

    response = client.post(
        reverse("admin:login"),
        {"username": PHONE, "password": "CustomerPass1", "next": "/admin/"},
    )

    assert response.status_code == 200
    assert "_auth_user_id" not in client.session
    assert customer.has_usable_password() is True


# ---------------------------------------------------------------------------
# The OTP login and the staff row
# ---------------------------------------------------------------------------
def test_otp_login_for_a_staff_phone_uses_a_separate_customer_account() -> None:
    _staff()
    client = Client()
    _start_otp(client)

    response = _verify(client, _code())

    assert response.status_code == 302
    customer = User.objects.get(phone=PHONE, is_staff=False)
    assert customer.role == User.ROLE_MEMBER
    assert client.session["_auth_user_id"] == str(customer.pk)


def test_otp_login_never_touches_the_staff_account() -> None:
    staff = _staff(email=EMAIL)
    client = Client()
    _start_otp(client)

    _verify(client, _code())

    staff.refresh_from_db()
    assert staff.check_password("StaffPass1") is True
    assert staff.is_staff is True
    assert staff.is_superuser is False
    assert staff.email == EMAIL
    assert staff.phone_verified_at is None
    assert staff.email_verified_at is None


# ---------------------------------------------------------------------------
# Printing and the session
# ---------------------------------------------------------------------------
def test_str_of_a_staff_account_says_staff() -> None:
    staff = _staff()

    assert str(staff) == f"{PHONE} (staff)"


def test_str_of_a_customer_account_is_the_phone() -> None:
    customer = _customer()

    assert str(customer) == PHONE


def test_staff_logged_in_can_still_open_the_shop_login_and_signup_pages() -> None:
    staff = _staff()
    client = Client()
    client.force_login(staff)

    assert client.get(reverse("account_login")).status_code == 200
    assert client.get(reverse("account_signup")).status_code == 200


def test_customer_login_replaces_the_staff_session() -> None:
    _customer()
    staff = _staff()
    client = Client()
    client.force_login(staff)

    response = client.post(
        reverse("account_login"),
        {"method": "password", "identifier": PHONE, "password": "CustomerPass1"},
    )

    assert response.status_code == 302
    customer = User.objects.get(phone=PHONE, is_staff=False)
    assert client.session["_auth_user_id"] == str(customer.pk)
