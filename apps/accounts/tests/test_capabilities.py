"""Tests for the capability defaults and has_capability (D75)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.capabilities import Capability, default_allowed, has_capability
from apps.accounts.models import RoleCapability, User
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _staff(role: str = User.ROLE_MEMBER, **kwargs):
    return UserFactory(is_staff=True, role=role, **kwargs)


def test_an_unknown_capability_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown capability"):
        has_capability(_staff(), "NOT_A_CAPABILITY")


def test_an_anonymous_user_has_no_capability() -> None:
    assert has_capability(AnonymousUser(), Capability.VIEW_ORDERS) is False


def test_an_inactive_staff_user_has_no_capability() -> None:
    user = _staff(is_active=False)

    assert has_capability(user, Capability.VIEW_ORDERS) is False


def test_a_non_staff_user_has_no_capability_even_with_a_staff_role() -> None:
    user = UserFactory(role=User.ROLE_MANAGER, is_staff=False)

    assert has_capability(user, Capability.VIEW_ORDERS) is False


def test_an_active_superuser_always_passes() -> None:
    user = UserFactory(is_staff=True, is_superuser=True, role=User.ROLE_MEMBER)

    assert has_capability(user, Capability.STOCK_WRITE) is True


def test_an_active_owner_always_passes() -> None:
    user = _staff(User.ROLE_OWNER)

    assert has_capability(user, Capability.EXPORT_CSV) is True


def test_a_manager_defaults_to_every_capability() -> None:
    user = _staff(User.ROLE_MANAGER)

    for capability in Capability.values:
        assert has_capability(user, capability) is True


def test_a_packer_defaults_to_view_start_and_send_out_only() -> None:
    user = _staff(User.ROLE_PACKER)

    assert has_capability(user, Capability.VIEW_ORDERS) is True
    assert has_capability(user, Capability.START_PACKING) is True
    assert has_capability(user, Capability.SEND_OUT) is True
    assert has_capability(user, Capability.STOCK_WRITE) is False
    assert has_capability(user, Capability.AUDIT_READ) is False
    assert has_capability(user, Capability.EXPORT_CSV) is False


def test_a_delivery_user_defaults_to_deliver_cod_only() -> None:
    user = _staff(User.ROLE_DELIVERY)

    assert has_capability(user, Capability.DELIVER_COD) is True
    assert has_capability(user, Capability.VIEW_ORDERS) is False


def test_a_member_defaults_to_no_capability() -> None:
    user = _staff()

    for capability in Capability.values:
        assert has_capability(user, capability) is False


def test_a_stored_row_can_take_a_default_away() -> None:
    user = _staff(User.ROLE_MANAGER)
    RoleCapability.objects.create(
        role=user.role,
        capability=Capability.STOCK_WRITE,
        allowed=False,
    )

    assert has_capability(user, Capability.STOCK_WRITE) is False
    assert has_capability(user, Capability.VIEW_ORDERS) is True


def test_a_stored_row_can_grant_a_capability_the_default_denies() -> None:
    user = _staff()
    RoleCapability.objects.create(
        role=user.role,
        capability=Capability.AUDIT_READ,
        allowed=True,
    )

    assert has_capability(user, Capability.AUDIT_READ) is True


def test_has_capability_reads_one_row_per_call() -> None:
    user = _staff(User.ROLE_PACKER)
    RoleCapability.objects.create(
        role=user.role,
        capability=Capability.STOCK_WRITE,
        allowed=False,
    )

    # One query for the stored row, and no cache between calls (D75).
    with CaptureQueriesContext(connection) as queries:
        has_capability(user, Capability.STOCK_WRITE)

    assert len(queries) == 1


def test_default_allowed_handles_an_unknown_role_as_empty() -> None:
    assert default_allowed("UNKNOWN", Capability.VIEW_ORDERS) is False
