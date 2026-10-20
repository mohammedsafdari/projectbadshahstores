"""Tests for the custom User model and phone normalisation.

All tests use PostgreSQL via pytest-django because they exercise conditional
unique constraints and IntegrityError behaviour.
"""

import re
from contextlib import nullcontext
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import IntegrityError, transaction
from django.core.exceptions import ValidationError
from django.test import Client

from apps.accounts.models import User
from apps.core.phone import normalise_phone


pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Phone normalisation
# ---------------------------------------------------------------------------

class TestNormalisePhone:
    VALID_INPUTS = [
        "9876543210",
        "+91 98765 43210",
        "09876543210",
        "98765-43210",
        "919876543210",
    ]

    def test_known_valid_phones_normalise_to_e164(self):
        for raw in self.VALID_INPUTS:
            assert normalise_phone(raw) == "+919876543210", (
                f"Expected +919876543210 for {raw!r}"
            )

    def test_leading_plus_stripped_and_rebuilt(self):
        assert normalise_phone("+919876543210") == "+919876543210"

    @pytest.mark.parametrize("bad_input", ["12345", "5876543210", "", "abc", "a1b2c"])
    def test_invalid_phones_raise_valueerror(self, bad_input):
        with pytest.raises(ValueError):
            normalise_phone(bad_input)

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            normalise_phone("   ")

    def test_email_like_strings_rejected(self):
        with pytest.raises(ValueError):
            normalise_phone("user@example.com")


# ---------------------------------------------------------------------------
# User creation
# ---------------------------------------------------------------------------

class TestUserCreation:
    def test_create_user_normalises_phone(self):
        user = User.objects.create_user(phone="98765 43210", password="ignore")
        assert user.phone == "+919876543210"

    def test_create_user_lowercases_and_nulls_email(self):
        user = User.objects.create_user(
            phone="9876543210",
            password="ignore",
            email="  USER@Example.COM ",
        )
        assert user.email == "user@example.com"

    def test_create_user_stores_empty_email_as_null(self):
        user = User.objects.create_user(
            phone="9876543210",
            password="ignore",
            email="",
        )
        assert user.email is None

    def test_create_user_without_password_has_unusable_password(self):
        user = User.objects.create_user(phone="9876543210")
        assert not user.has_usable_password()

    def test_create_user_with_password_has_usable_password(self):
        user = User.objects.create_user(phone="9876543210", password="ok")
        assert user.has_usable_password()

    def test_create_user_without_phone_raises(self):
        with pytest.raises(ValueError, match="phone"):
            User.objects.create_user(phone="")

    def test_create_user_without_phone_none_raises(self):
        with pytest.raises(ValueError, match="phone"):
            User.objects.create_user(phone=None)  # type: ignore[arg-type]

    def test_default_role_is_member(self):
        user = User.objects.create_user(phone="9876543210", password="ignore")
        assert user.role == User.ROLE_MEMBER


class TestCreateSuperuser:
    def test_create_superuser_has_staff_and_superuser(self):
        user = User.objects.create_superuser(phone="9876543210", password="ok")
        assert user.is_staff is True
        assert user.is_superuser is True

    def test_create_superuser_has_owner_role(self):
        user = User.objects.create_superuser(phone="9876543210", password="ok")
        assert user.role == User.ROLE_OWNER

    def test_create_superuser_without_password_raises(self):
        with pytest.raises(ValueError, match="password"):
            User.objects.create_superuser(phone="9876543210")


# ---------------------------------------------------------------------------
# Unique constraints
# ---------------------------------------------------------------------------

class TestPhoneUniqueness:
    def test_two_active_users_with_same_phone_fail(self):
        User.objects.create_user(phone="9876543210", password="ignore")
        with transaction.atomic():
            with pytest.raises(IntegrityError):
                User.objects.create_user(phone="9876543210", password="ignore")

    def test_inactive_duplicate_phone_allowed(self):
        active = User.objects.create_user(phone="9876543210", password="ignore")
        inactive = User.objects.create_user(phone="9999999999", password="ignore")
        inactive.is_active = False
        inactive.save()
        # The active user still exists; creating another active with same phone
        # should still fail, but an inactive duplicate of the active phone is
        # not possible to create because phone is normalised per-row. Instead
        # we verify that an inactive user can share a phone that would otherwise
        # be unique among active rows by deactivating the original.
        active.is_active = False
        active.save()
        duplicate = User.objects.create_user(phone="9876543210", password="ignore")
        assert duplicate.phone == "+919876543210"


class TestEmailUniqueness:
    def test_two_users_with_email_none_both_succeed(self):
        User.objects.create_user(phone="9876543210", password="ignore", email=None)
        User.objects.create_user(phone="9876543211", password="ignore", email=None)
        assert User.objects.filter(email__isnull=True).count() == 2

    def test_empty_string_email_stored_as_null(self):
        user = User.objects.create_user(
            phone="9876543210",
            password="ignore",
            email="",
        )
        assert user.email is None

    def test_email_stored_lowercase(self):
        user = User.objects.create_user(
            phone="9876543210",
            password="ignore",
            email="  UpPer@Example.COM ",
        )
        assert user.email == "upper@example.com"

    def test_two_active_users_with_same_email_fail(self):
        User.objects.create_user(
            phone="9876543210",
            password="ignore",
            email="dup@example.com",
        )
        with transaction.atomic():
            with pytest.raises(IntegrityError):
                User.objects.create_user(
                    phone="9876543211",
                    password="ignore",
                    email="DUP@example.com",
                )


# ---------------------------------------------------------------------------
# Manager natural key
# ---------------------------------------------------------------------------

class TestGetByNaturalKey:
    def test_finds_active_user_with_spaced_phone(self):
        user = User.objects.create_user(phone="9876543210", password="ignore")
        found = User.objects.get_by_natural_key("98765 43210")
        assert found.pk == user.pk
        assert found.phone == "+919876543210"

    def test_invalid_phone_raises_doesnotexist(self):
        with pytest.raises(User.DoesNotExist):
            User.objects.get_by_natural_key("12345")

    def test_empty_phone_raises_doesnotexist(self):
        with pytest.raises(User.DoesNotExist):
            User.objects.get_by_natural_key("")

    def test_inactive_user_not_found(self):
        user = User.objects.create_user(phone="9876543210", password="ignore")
        user.is_active = False
        user.save()
        with pytest.raises(User.DoesNotExist):
            User.objects.get_by_natural_key("98765 43210")

    def test_various_invalid_shapes_raise_doesnotexist(self):
        for bad in ["12345", "5876543210", "", "abc"]:
            with pytest.raises(User.DoesNotExist):
                User.objects.get_by_natural_key(bad)


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------

class TestUserClean:
    def test_clean_invalid_phone_raises_validation_error(self):
        user = User(phone="12345")
        with pytest.raises(ValidationError) as exc:
            user.clean()
        assert "phone" in exc.value.message_dict

    def test_clean_empty_phone_raises_validation_error(self):
        user = User(phone="")
        with pytest.raises(ValidationError) as exc:
            user.clean()
        assert "phone" in exc.value.message_dict

    def test_clean_valid_phone_does_not_raise(self):
        user = User(phone="9876543210")
        user.clean()


# ---------------------------------------------------------------------------
# Admin access: role is NOT the authority source
# ---------------------------------------------------------------------------

class TestAdminAccessDerivesFromStaffNotRole:
    def test_owner_without_staff_is_redirected_to_login(self):
        user = User.objects.create_user(
            phone="9876543210",
            password="ok",
            role=User.ROLE_OWNER,
            is_staff=False,
        )
        client = Client()
        client.force_login(user)
        response = client.get("/admin/")
        assert response.status_code in (301, 302)
        location = response.headers.get("Location", "")
        assert "/admin/login" in location or "login" in location.lower()
        assert user.is_staff is False

    def test_user_with_staff_true_opens_admin(self):
        user = User.objects.create_user(
            phone="9876543211",
            password="ok",
            role=User.ROLE_MEMBER,
            is_staff=True,
        )
        client = Client()
        client.force_login(user)
        response = client.get("/admin/")
        # A staff user reaches the admin index (or a redirect to a dashboard);
        # either way it is not a login redirect.
        assert response.status_code in (200, 301, 302, 303, 307, 308)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location", "")
            assert "/admin/login" not in location
        # Explicitly confirm staff access derives from is_staff, not role.
        assert user.is_staff is True
        assert user.role == User.ROLE_MEMBER

    def test_owner_without_staff_has_no_staff_flag(self):
        user = User.objects.create_user(
            phone="9876543212",
            password="ok",
            role=User.ROLE_OWNER,
            is_staff=False,
        )
        assert user.is_staff is False
