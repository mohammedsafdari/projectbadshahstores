"""Tests for the verification timestamps in the User admin (D71).

The helpers match ``test_admin.py``: a real admin site, a RequestFactory
request and a logged-in test client.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib import admin
from django.test import Client, RequestFactory

from apps.accounts.admin import UserAdmin
from apps.accounts.models import User
from apps.core.phone import normalise_phone
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

_request_factory = RequestFactory()


def client_as(phone):
    """Return a test client logged in as the user identified by phone."""
    user = User.objects.get(phone=normalise_phone(phone))
    client = Client()
    client.force_login(user)
    return client


def admin_with_user(user):
    """Return a UserAdmin bound to the real admin site with request.user set."""
    request = _request_factory.get("/")
    request.user = user
    return UserAdmin(User, admin.site), request


def test_user_admin_shows_the_verified_times_read_only() -> None:
    user = UserFactory()
    admin_user = User.objects.create_superuser(phone="9876543210", password="ok")
    user_admin, request = admin_with_user(admin_user)

    readonly = user_admin.get_readonly_fields(request, obj=user)
    dates = dict(user_admin.fieldsets)["Important dates"]["fields"]

    assert "phone_verified_at" in readonly
    assert "email_verified_at" in readonly
    assert "phone_verified_at" in dates
    assert "email_verified_at" in dates


def test_changing_the_email_in_the_admin_clears_the_email_verification() -> None:
    user = UserFactory(email="asha@example.com")
    admin_user = User.objects.create_superuser(phone="9876543210", password="ok")
    user_admin, request = admin_with_user(admin_user)

    user_admin.save_model(request, user, SimpleNamespace(changed_data=["email"]), change=True)

    user.refresh_from_db()
    assert user.email_verified_at is None
    # Only the changed contact detail is cleared.
    assert user.phone_verified_at is not None


def test_changing_the_phone_in_the_admin_clears_the_phone_verification() -> None:
    user = UserFactory()
    admin_user = User.objects.create_superuser(phone="9876543210", password="ok")
    user_admin, request = admin_with_user(admin_user)

    user_admin.save_model(request, user, SimpleNamespace(changed_data=["phone"]), change=True)

    user.refresh_from_db()
    assert user.phone_verified_at is None
    assert user.email_verified_at is not None


def test_saving_without_changes_keeps_the_verified_times() -> None:
    user = UserFactory()
    phone_time = user.phone_verified_at
    email_time = user.email_verified_at
    admin_user = User.objects.create_superuser(phone="9876543210", password="ok")
    user_admin, request = admin_with_user(admin_user)

    user_admin.save_model(request, user, SimpleNamespace(changed_data=[]), change=True)

    user.refresh_from_db()
    assert user.phone_verified_at == phone_time
    assert user.email_verified_at == email_time


def test_admin_cannot_set_the_verified_times_directly() -> None:
    user = UserFactory(phone_verified_at=None, email_verified_at=None)
    User.objects.create_superuser(phone="9876543210", password="ok")
    client = client_as("+919876543210")

    html = client.get(f"/admin/accounts/user/{user.pk}/change/").content.decode()

    assert 'name="phone_verified_at"' not in html
    assert 'name="email_verified_at"' not in html
