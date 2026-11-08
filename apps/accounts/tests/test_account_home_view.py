"""Tests for the account home page and the header link (D73)."""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.auth_presentation import (
    ADD_EMAIL_LINK,
    CHANGE_PASSWORD_LINK,
    NOT_VERIFIED_MARK,
    SET_PASSWORD_LINK,
    VERIFIED_MARK,
)
from apps.accounts.models import User
from apps.accounts.verification_presentation import mask_email, mask_phone

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
EMAIL = "asha@example.com"
PASSWORD = "StrongPass1"


def _customer(
    *,
    phone: str = PHONE,
    full_name: str = "Asha Sharma",
    email: str | None = EMAIL,
    password: str | None = PASSWORD,
) -> User:
    return User.objects.create_user(
        phone=phone,
        password=password,
        email=email,
        full_name=full_name,
    )


def _element_text(html: str, marker: str) -> str:
    """The text inside the first element that carries this data marker."""
    pattern = (
        rf"<(?P<tag>[a-z0-9]+)\b[^>]*\b{re.escape(marker)}\b[^>]*>"
        rf"(?P<body>.*?)</(?P=tag)>"
    )
    match = re.search(pattern, html, re.DOTALL)
    assert match is not None, f"No element with {marker}."
    return re.sub(r"<[^>]+>", "", match.group("body")).strip()


def _home_html(user: User) -> str:
    client = Client()
    client.force_login(user)
    return client.get(reverse("account_home")).content.decode()


def test_account_home_requires_login() -> None:
    response = Client().get(reverse("account_home"))

    assert response.status_code == 302
    assert response.url.startswith(f"{reverse('account_login')}?next=")


def test_account_home_shows_the_name_and_masked_contacts() -> None:
    html = _home_html(_customer())

    assert "Asha Sharma" in html
    assert mask_phone(PHONE) in html
    assert mask_email(EMAIL) in html


def test_account_home_shows_verified_and_not_verified_marks() -> None:
    user = _customer()
    user.phone_verified_at = timezone.now()
    user.save(update_fields=["phone_verified_at", "updated_at"])

    html = _home_html(user)

    assert _element_text(html, "data-phone-status") == str(VERIFIED_MARK)
    assert _element_text(html, "data-email-status") == str(NOT_VERIFIED_MARK)


def test_account_home_links_to_the_verify_pages_only_when_something_is_pending() -> None:
    pending = _home_html(_customer())
    # The two accounts need different phones: two active customers cannot share
    # one phone (D72).
    ready = _customer(phone="+919876543211", email=None)
    ready.phone_verified_at = timezone.now()
    ready.save(update_fields=["phone_verified_at", "updated_at"])
    ready_html = _home_html(ready)

    assert 'data-link-kind="verify-phone"' in pending
    assert 'data-link-kind="verify-email"' in pending
    assert 'data-link-kind="verify-phone"' not in ready_html
    assert 'data-link-kind="verify-email"' not in ready_html


def test_account_home_offers_to_set_a_password_for_an_otp_only_customer() -> None:
    html = _home_html(_customer(password=None))

    assert str(SET_PASSWORD_LINK) in html
    assert 'data-link-kind="password"' in html


def test_account_home_offers_to_change_the_password_when_one_exists() -> None:
    html = _home_html(_customer())

    assert str(CHANGE_PASSWORD_LINK) in html
    assert str(SET_PASSWORD_LINK) not in html


def test_account_home_offers_to_add_an_email_when_there_is_none() -> None:
    html = _home_html(_customer(email=None))

    assert str(ADD_EMAIL_LINK) in html
    assert "data-account-email" not in html
    assert 'data-link-kind="email"' in html


def test_account_home_links_to_orders_and_addresses() -> None:
    html = _home_html(_customer())

    assert f'href="{reverse("order_list")}"' in html
    assert f'href="{reverse("address_list")}"' in html
    assert 'data-link-kind="orders"' in html
    assert 'data-link-kind="addresses"' in html


def test_account_home_is_not_cached() -> None:
    client = Client()
    client.force_login(_customer())

    response = client.get(reverse("account_home"))

    assert "no-store" in response["Cache-Control"]


def test_account_home_rejects_post() -> None:
    client = Client()
    client.force_login(_customer())

    response = client.post(reverse("account_home"))

    assert response.status_code == 405


def test_header_shows_the_my_account_link_for_a_customer() -> None:
    client = Client()
    client.force_login(_customer())

    html = client.get(reverse("home")).content.decode()

    assert "data-my-account-link" in html
    assert f'href="{reverse("account_home")}"' in html


def test_header_hides_the_my_account_link_for_a_guest() -> None:
    html = Client().get(reverse("home")).content.decode()

    assert "data-my-account-link" not in html
