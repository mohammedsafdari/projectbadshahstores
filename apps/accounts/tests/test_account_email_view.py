"""Tests for the account email page (D73)."""

from __future__ import annotations

import re

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.auth_presentation import (
    CURRENT_PASSWORD_WRONG_MESSAGE,
    EMAIL_UNUSABLE_MESSAGE,
)
from apps.accounts.models import OtpChallenge, User
from apps.accounts.passwords import login_locked
from apps.accounts.verification import confirm_email_code, request_email_code

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
EMAIL = "asha@example.com"
NEW_EMAIL = "asha.new@example.com"
PASSWORD = "StrongPass1"


@pytest.fixture(autouse=True)
def _clear_cache():
    # The login throttle lives in the cache, so no test inherits a lock.
    cache.clear()
    yield
    cache.clear()


def _customer(
    *,
    email: str | None = EMAIL,
    password: str | None = PASSWORD,
    phone_verified: bool = True,
    email_verified: bool = True,
) -> User:
    user = User.objects.create_user(phone=PHONE, password=password, email=email)
    user.phone_verified_at = timezone.now() if phone_verified else None
    user.email_verified_at = timezone.now() if email_verified else None
    user.save(update_fields=["phone_verified_at", "email_verified_at", "updated_at"])
    return user


def _logged_in(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _page_path() -> str:
    return reverse("account_email")


def _post(
    client: Client,
    email: str,
    *,
    current_password: str | None = PASSWORD,
):
    data = {"email": email}
    if current_password is not None:
        data["current_password"] = current_password
    return client.post(_page_path(), data)


def _email_code(mailoutbox) -> str:
    match = re.search(r"\b(\d{6})\b", mailoutbox[-1].body)
    assert match is not None
    return match.group(1)


def test_email_page_requires_login() -> None:
    response = Client().get(_page_path())

    assert response.status_code == 302
    assert response.url.startswith(f"{reverse('account_login')}?next=")


def test_email_page_shows_the_current_email_and_asks_for_the_password_when_one_exists() -> None:
    html = _logged_in(_customer()).get(_page_path()).content.decode()

    assert f'value="{EMAIL}"' in html
    assert "data-current-password-field" in html
    assert 'name="current_password"' in html


def test_otp_only_customer_can_add_a_first_email_without_a_password() -> None:
    user = _customer(email=None, password=None)
    client = _logged_in(user)
    html = client.get(_page_path()).content.decode()
    assert "data-current-password-field" not in html

    response = _post(client, NEW_EMAIL, current_password=None)

    assert response.status_code == 302
    assert response["Location"] == reverse("account_verify_email")
    user.refresh_from_db()
    assert user.email == NEW_EMAIL
    assert user.email_verified_at is None


# The slice prompt fixes this test name, so the "def" line cannot fit in 100
# characters; there is no room for the usual "-> None" hint on this one test.
def test_email_change_with_the_right_password_clears_the_email_verification_and_goes_to_the_verify_page():  # noqa: E501
    user = _customer()
    before = user.updated_at
    client = _logged_in(user)

    response = _post(client, NEW_EMAIL)

    assert response.status_code == 302
    assert response["Location"] == reverse("account_verify_email")
    user.refresh_from_db()
    assert user.email == NEW_EMAIL
    assert user.email_verified_at is None
    # The save(update_fields=...) writes updated_at as well.
    assert user.updated_at > before


def test_unchanged_email_changes_nothing_and_keeps_the_verification() -> None:
    user = _customer()
    client = _logged_in(user)

    response = _post(client, EMAIL)

    assert response.status_code == 302
    assert response["Location"] == _page_path()
    user.refresh_from_db()
    assert user.email == EMAIL
    assert user.email_verified_at is not None


def test_email_change_with_a_wrong_password_shows_an_error_and_changes_nothing() -> None:
    user = _customer()
    client = _logged_in(user)

    response = _post(client, NEW_EMAIL, current_password="WrongPass9")

    assert response.status_code == 200
    assert str(CURRENT_PASSWORD_WRONG_MESSAGE) in response.content.decode()
    user.refresh_from_db()
    assert user.email == EMAIL
    assert user.email_verified_at is not None


def test_wrong_password_on_the_email_page_counts_toward_the_login_lock() -> None:
    user = _customer()
    client = _logged_in(user)

    for _attempt in range(5):
        _post(client, NEW_EMAIL, current_password="WrongPass9")

    assert login_locked(user) is True


def test_email_used_by_another_customer_is_refused_with_a_generic_message() -> None:
    user = _customer()
    User.objects.create_user(phone="+919876543211", email=NEW_EMAIL)
    client = _logged_in(user)

    response = _post(client, NEW_EMAIL)

    assert response.status_code == 200
    assert str(EMAIL_UNUSABLE_MESSAGE) in response.content.decode()
    user.refresh_from_db()
    assert user.email == EMAIL


def test_email_used_by_a_staff_account_is_allowed() -> None:
    user = _customer()
    User.objects.create_user(
        phone="+919876543211",
        email=NEW_EMAIL,
        password=PASSWORD,
        is_staff=True,
    )
    client = _logged_in(user)

    response = _post(client, NEW_EMAIL)

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.email == NEW_EMAIL


def test_invalid_email_is_refused() -> None:
    user = _customer()
    client = _logged_in(user)

    response = _post(client, "not-an-email")

    assert response.status_code == 200
    assert "email" in response.context["form"].errors
    user.refresh_from_db()
    assert user.email == EMAIL


def test_email_is_stored_in_lowercase() -> None:
    user = _customer()
    client = _logged_in(user)

    _post(client, " Asha.New@Example.COM ")

    user.refresh_from_db()
    assert user.email == "asha.new@example.com"


def test_email_change_keeps_the_phone_verification() -> None:
    user = _customer()
    verified_at = user.phone_verified_at
    client = _logged_in(user)

    _post(client, NEW_EMAIL)

    user.refresh_from_db()
    assert user.phone_verified_at == verified_at
    assert user.phone_verified_at is not None


def test_email_change_closes_pending_email_codes(mailoutbox) -> None:
    user = _customer(email_verified=False)
    request_email_code(user)
    assert OtpChallenge.objects.filter(consumed_at__isnull=True).count() == 1
    client = _logged_in(user)

    _post(client, NEW_EMAIL)

    assert (
        OtpChallenge.objects.filter(
            purpose=OtpChallenge.Purpose.EMAIL_VERIFY,
            consumed_at__isnull=True,
        ).count()
        == 0
    )


def test_old_email_code_cannot_verify_the_new_email(mailoutbox) -> None:
    user = _customer(email_verified=False)
    request_email_code(user)
    old_code = _email_code(mailoutbox)
    client = _logged_in(user)
    _post(client, NEW_EMAIL)

    worked = confirm_email_code(user, old_code)

    assert worked is False
    user.refresh_from_db()
    assert user.email_verified_at is None


def test_email_page_needs_a_csrf_token() -> None:
    user = _customer()
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)

    response = _post(client, NEW_EMAIL)

    assert response.status_code == 403
    user.refresh_from_db()
    assert user.email == EMAIL


def test_email_page_rejects_put() -> None:
    client = _logged_in(_customer())

    response = client.put(_page_path())

    assert response.status_code == 405


def test_email_page_is_not_cached() -> None:
    response = _logged_in(_customer()).get(_page_path())

    assert "no-store" in response["Cache-Control"]
