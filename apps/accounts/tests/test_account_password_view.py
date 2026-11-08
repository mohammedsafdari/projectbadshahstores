"""Tests for the account password page (D73)."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.accounts.auth_presentation import (
    CURRENT_PASSWORD_WRONG_MESSAGE,
    LOGIN_LOCKED_MESSAGE,
)
from apps.accounts.models import User
from apps.accounts.passwords import (
    REASON_OK,
    authenticate_customer_password,
    login_locked,
    record_login_failure,
)

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
OLD_PASSWORD = "OldPass123"
NEW_PASSWORD = "NewPass456"


@pytest.fixture(autouse=True)
def _clear_cache():
    # The login throttle lives in the cache, so no test inherits a lock.
    cache.clear()
    yield
    cache.clear()


def _customer(*, password: str | None = OLD_PASSWORD) -> User:
    return User.objects.create_user(phone=PHONE, password=password)


def _logged_in(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _page_path() -> str:
    return reverse("account_password")


def _post(
    client: Client,
    *,
    current_password: str | None = None,
    password1: str = NEW_PASSWORD,
    password2: str = NEW_PASSWORD,
):
    data = {"new_password1": password1, "new_password2": password2}
    if current_password is not None:
        data["current_password"] = current_password
    return client.post(_page_path(), data)


def test_password_page_requires_login() -> None:
    response = Client().get(_page_path())

    assert response.status_code == 302
    assert response.url.startswith(f"{reverse('account_login')}?next=")


def test_password_page_asks_for_the_current_password_when_one_exists() -> None:
    html = _logged_in(_customer()).get(_page_path()).content.decode()

    assert "data-current-password-field" in html
    assert 'name="current_password"' in html


def test_password_page_has_no_current_password_field_for_an_otp_only_customer() -> None:
    html = _logged_in(_customer(password=None)).get(_page_path()).content.decode()

    assert "data-current-password-field" not in html
    assert 'name="current_password"' not in html


def test_password_change_with_the_right_current_password_works() -> None:
    user = _customer()
    before = user.updated_at
    client = _logged_in(user)

    response = _post(client, current_password=OLD_PASSWORD)

    assert response.status_code == 302
    assert response["Location"] == reverse("account_home")
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD) is True
    # The save(update_fields=...) writes updated_at as well.
    assert user.updated_at > before


def test_password_change_keeps_the_customer_logged_in() -> None:
    user = _customer()
    client = _logged_in(user)
    _post(client, current_password=OLD_PASSWORD)

    response = client.get(reverse("account_home"))

    assert response.status_code == 200
    assert client.session["_auth_user_id"] == str(user.pk)


def test_password_change_with_a_wrong_current_password_shows_an_error_and_changes_nothing() -> None:
    user = _customer()
    client = _logged_in(user)

    response = _post(client, current_password="WrongPass9")

    assert response.status_code == 200
    assert str(CURRENT_PASSWORD_WRONG_MESSAGE) in response.content.decode()
    user.refresh_from_db()
    assert user.check_password(OLD_PASSWORD) is True
    assert user.check_password(NEW_PASSWORD) is False


def test_wrong_current_password_counts_toward_the_login_lock() -> None:
    user = _customer()
    client = _logged_in(user)

    for _attempt in range(5):
        _post(client, current_password="WrongPass9")

    assert login_locked(user) is True


def test_locked_account_cannot_change_the_password_even_with_the_right_current_password() -> None:
    user = _customer()
    for _attempt in range(5):
        record_login_failure(user)
    assert login_locked(user) is True
    client = _logged_in(user)

    response = _post(client, current_password=OLD_PASSWORD)

    assert response.status_code == 200
    assert str(LOGIN_LOCKED_MESSAGE) in response.content.decode()
    user.refresh_from_db()
    assert user.check_password(OLD_PASSWORD) is True


def test_password_change_refuses_a_weak_password() -> None:
    user = _customer()
    client = _logged_in(user)

    response = _post(
        client,
        current_password=OLD_PASSWORD,
        password1="12345678",
        password2="12345678",
    )

    assert response.status_code == 200
    assert "new_password1" in response.context["form"].errors
    user.refresh_from_db()
    assert user.check_password(OLD_PASSWORD) is True


def test_password_change_refuses_different_passwords() -> None:
    user = _customer()
    client = _logged_in(user)

    response = _post(
        client,
        current_password=OLD_PASSWORD,
        password1="StrongPass1",
        password2="OtherPass2",
    )

    assert response.status_code == 200
    assert "new_password2" in response.context["form"].errors
    user.refresh_from_db()
    assert user.check_password(OLD_PASSWORD) is True


def test_otp_only_customer_can_set_a_first_password() -> None:
    user = _customer(password=None)
    client = _logged_in(user)

    response = _post(client)

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.has_usable_password() is True
    assert user.check_password(NEW_PASSWORD) is True


def test_new_password_works_for_password_login() -> None:
    user = _customer()
    client = _logged_in(user)
    _post(client, current_password=OLD_PASSWORD)

    result = authenticate_customer_password(PHONE, NEW_PASSWORD)

    assert result.reason == REASON_OK


def test_password_page_needs_a_csrf_token() -> None:
    user = _customer()
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)

    response = _post(client, current_password=OLD_PASSWORD)

    assert response.status_code == 403
    user.refresh_from_db()
    assert user.check_password(OLD_PASSWORD) is True


def test_password_page_rejects_put() -> None:
    client = _logged_in(_customer())

    response = client.put(_page_path())

    assert response.status_code == 405


def test_password_page_is_not_cached() -> None:
    response = _logged_in(_customer()).get(_page_path())

    assert "no-store" in response["Cache-Control"]


def test_password_page_never_echoes_a_posted_password() -> None:
    user = _customer()
    client = _logged_in(user)

    response = _post(
        client,
        current_password="WrongPass9",
        password1=NEW_PASSWORD,
        password2=NEW_PASSWORD,
    )
    html = response.content.decode()

    assert "WrongPass9" not in html
    assert NEW_PASSWORD not in html
