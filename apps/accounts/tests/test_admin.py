"""Tests for the custom User admin UI.

All tests use PostgreSQL via pytest-django and the Django test client.
"""

import pytest
from django.contrib import admin
from django.test import Client, RequestFactory

from apps.accounts.admin import UserAdmin
from apps.accounts.models import User
from apps.core.phone import normalise_phone

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


class TestUserAdminPages:
    def test_superuser_can_open_add_page(self):
        User.objects.create_superuser(
            phone="9876543210",
            password="ok",
        )
        client = client_as("+919876543210")
        response = client.get("/admin/accounts/user/add/")
        assert response.status_code == 200

    def test_superuser_can_open_change_page(self):
        user = User.objects.create_user(phone="9876543211", password="ok")
        User.objects.create_superuser(phone="9876543210", password="ok")
        client = client_as("+919876543210")
        response = client.get(f"/admin/accounts/user/{user.pk}/change/")
        assert response.status_code == 200

    def test_change_page_does_not_render_password_input(self):
        user = User.objects.create_user(phone="9876543211", password="ok")
        User.objects.create_superuser(phone="9876543210", password="ok")
        client = client_as("+919876543210")
        response = client.get(f"/admin/accounts/user/{user.pk}/change/")
        html = response.content.decode("utf-8", "replace")
        assert 'type="password"' not in html.lower()
        assert 'name="password"' not in html


class TestAddUserFormCreatesUser:
    def test_add_form_creates_user_with_normalised_phone_and_hashed_password(self):
        superuser = User.objects.create_superuser(phone="9876543210", password="ok")
        client = client_as("+919876543210")
        response = client.post(
            "/admin/accounts/user/add/",
            {
                "phone": "98765 43211",
                "password1": "StrongPass1",
                "password2": "StrongPass1",
                "full_name": "Test User",
                "role": "MEMBER",
                "is_active": "on",
                "is_staff": "on",
            },
            follow=False,
        )
        assert response.status_code == 302, response.context["adminform"].form.errors
        new_user = User.objects.get(phone="+919876543211")
        assert new_user.full_name == "Test User"
        assert new_user.phone == "+919876543211"
        assert new_user.has_usable_password()
        assert new_user.check_password("StrongPass1")
        assert new_user.pk != superuser.pk

    def test_duplicate_active_phone_returns_form_error(self):
        superuser = User.objects.create_superuser(phone="9876543210", password="ok")
        client = client_as("+919876543210")
        response = client.post(
            "/admin/accounts/user/add/",
            {
                "phone": "98765 43210",
                "password1": "StrongPass1",
                "password2": "StrongPass1",
                "full_name": "Dup User",
                "is_active": "on",
                "is_staff": "on",
            },
            follow=True,
        )
        assert response.status_code == 200
        adminform = response.context.get("adminform")
        if adminform is not None:
            form = adminform.form
            assert form.errors
            assert "phone" in form.errors
        else:
            errors = response.context.get("errors")
            assert errors
            assert "phone" in errors
        assert not User.objects.filter(phone="+919876543210").exclude(pk=superuser.pk).exists()

    def test_invalid_phone_returns_form_error_and_no_user(self):
        User.objects.create_superuser(phone="9876543210", password="ok")
        before = set(User.objects.values_list("pk", flat=True))
        client = client_as("+919876543210")
        response = client.post(
            "/admin/accounts/user/add/",
            {
                "phone": "12345",
                "password1": "StrongPass1",
                "password2": "StrongPass1",
                "full_name": "Bad User",
                "is_active": "on",
                "is_staff": "on",
            },
            follow=True,
        )
        assert response.status_code == 200
        adminform = response.context.get("adminform")
        if adminform is not None:
            form = adminform.form
            assert form.errors
            assert "phone" in form.errors
        else:
            errors = response.context.get("errors")
            assert errors
            assert "phone" in errors
        after = set(User.objects.values_list("pk", flat=True))
        assert after == before


class TestNonSuperuserCannotEditSuperuserFlags:
    def test_staff_user_readonly_fields_include_superuser_groups_and_permissions(self):
        staff = User.objects.create_user(
            phone="9876543211",
            password="ok",
            is_staff=True,
        )
        admin, request = admin_with_user(staff)
        readonly = admin.get_readonly_fields(request, obj=None)
        assert "is_superuser" in readonly
        assert "groups" in readonly
        assert "user_permissions" in readonly

    def test_superuser_readonly_fields_do_not_include_superuser_flags(self):
        superuser = User.objects.create_superuser(phone="9876543210", password="ok")
        admin, request = admin_with_user(superuser)
        readonly = admin.get_readonly_fields(request, obj=None)
        assert "is_superuser" not in readonly
        assert "groups" not in readonly
        assert "user_permissions" not in readonly
