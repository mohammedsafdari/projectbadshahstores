"""The admin add form keeps staff and customer account spaces apart (D72)."""

from __future__ import annotations

import pytest
from django.test import Client

from apps.accounts.models import User

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
OTHER_PHONE = "+919876543211"
EMAIL = "asha@example.com"
PASSWORD = "StrongPass1"


def _client() -> Client:
    superuser = User.objects.create_superuser(phone="+919876543299", password=PASSWORD)
    client = Client()
    client.force_login(superuser)
    return client


def _post(client: Client, **overrides: str):
    # ``**overrides`` collects the changes into a dict, so each test sets only
    # the field it cares about and the rest stay the same.
    data = {
        "phone": OTHER_PHONE,
        "password1": PASSWORD,
        "password2": PASSWORD,
        "full_name": "Test User",
        "email": "",
        "role": "MEMBER",
        "is_active": "on",
        "is_staff": "",
    }
    data.update(overrides)
    return client.post("/admin/accounts/user/add/", data)


def _form(response):
    return response.context["adminform"].form


def test_admin_add_form_allows_a_staff_account_for_a_customers_phone() -> None:
    User.objects.create_user(phone=PHONE, password=PASSWORD, email=EMAIL)
    client = _client()

    response = _post(client, phone=PHONE, is_staff="on")

    assert response.status_code == 302
    assert User.objects.filter(phone=PHONE).count() == 2
    assert User.objects.filter(phone=PHONE, is_staff=True).count() == 1


def test_admin_add_form_allows_a_staff_account_for_a_customers_email() -> None:
    User.objects.create_user(phone=PHONE, password=PASSWORD, email=EMAIL)
    client = _client()

    response = _post(client, phone=OTHER_PHONE, email=EMAIL, is_staff="on")

    assert response.status_code == 302
    assert User.objects.filter(email=EMAIL).count() == 2
    assert User.objects.filter(email=EMAIL, is_staff=True).count() == 1


def test_admin_add_form_refuses_a_second_staff_account_with_the_same_phone() -> None:
    User.objects.create_user(phone=PHONE, password=PASSWORD, is_staff=True)
    client = _client()

    response = _post(client, phone=PHONE, is_staff="on")

    assert response.status_code == 200
    assert "phone" in _form(response).errors
    assert User.objects.filter(phone=PHONE).count() == 1


def test_admin_add_form_refuses_a_second_customer_account_with_the_same_phone() -> None:
    User.objects.create_user(phone=PHONE, password=PASSWORD)
    client = _client()

    response = _post(client, phone=PHONE)

    assert response.status_code == 200
    assert "phone" in _form(response).errors
    assert User.objects.filter(phone=PHONE).count() == 1
