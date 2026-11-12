"""Tests for the promotions capability (D81, D75)."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from apps.accounts.capabilities import Capability, has_capability
from apps.accounts.models import RoleCapability, User
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

MATRIX_URL = "admin:accounts_rolecapability_matrix"


def _staff(role: str) -> User:
    return UserFactory(is_staff=True, role=role)


def test_the_owner_and_manager_hold_manage_promotions() -> None:
    assert has_capability(_staff(User.ROLE_OWNER), Capability.MANAGE_PROMOTIONS) is True
    assert has_capability(_staff(User.ROLE_MANAGER), Capability.MANAGE_PROMOTIONS) is True


def test_the_packer_delivery_and_member_do_not() -> None:
    for role in (User.ROLE_PACKER, User.ROLE_DELIVERY, User.ROLE_MEMBER):
        assert has_capability(_staff(role), Capability.MANAGE_PROMOTIONS) is False


def test_the_label_is_the_agreed_sentence() -> None:
    assert Capability.MANAGE_PROMOTIONS.label == "Manage coupons, banners and featured products"


def test_the_key_is_manage_promotions() -> None:
    assert Capability.MANAGE_PROMOTIONS.value == "MANAGE_PROMOTIONS"


def test_the_owner_matrix_shows_the_new_row() -> None:
    client = Client()
    client.force_login(_staff(User.ROLE_OWNER))

    html = client.get(reverse(MATRIX_URL)).content.decode()

    assert 'name="cap_MANAGER_MANAGE_PROMOTIONS"' in html
    assert "Manage coupons, banners and featured products" in html


def test_a_stored_override_grants_it_to_a_packer() -> None:
    packer = _staff(User.ROLE_PACKER)
    assert has_capability(packer, Capability.MANAGE_PROMOTIONS) is False

    RoleCapability.objects.create(
        role=packer.role,
        capability=Capability.MANAGE_PROMOTIONS,
        allowed=True,
    )

    assert has_capability(packer, Capability.MANAGE_PROMOTIONS) is True


def test_a_stored_override_takes_it_from_the_manager() -> None:
    manager = _staff(User.ROLE_MANAGER)

    RoleCapability.objects.create(
        role=manager.role,
        capability=Capability.MANAGE_PROMOTIONS,
        allowed=False,
    )

    assert has_capability(manager, Capability.MANAGE_PROMOTIONS) is False


def test_the_migration_state_is_clean() -> None:
    # ``--check`` raises SystemExit when a model change still needs a migration.
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)
