"""Tests for set_role_capabilities: changes, audits and no-ops (D75)."""

from __future__ import annotations

import pytest

from apps.accounts.capabilities import Capability
from apps.accounts.capability_services import (
    ROLE_CAPABILITY_ACTION,
    RoleCapabilityError,
    set_role_capabilities,
)
from apps.accounts.models import RoleCapability, User
from apps.core.models import AuditLog
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _owner() -> User:
    return UserFactory(is_staff=True, role=User.ROLE_OWNER)


def test_an_owner_can_change_a_capability() -> None:
    actor = _owner()

    changed = set_role_capabilities(
        actor,
        User.ROLE_PACKER,
        {Capability.STOCK_WRITE: True},
    )

    assert changed == [Capability.STOCK_WRITE]
    row = RoleCapability.objects.get(role=User.ROLE_PACKER, capability=Capability.STOCK_WRITE)
    assert row.allowed is True
    assert row.updated_by_id == actor.pk


def test_a_superuser_can_change_a_capability() -> None:
    actor = UserFactory(is_staff=True, is_superuser=True, role=User.ROLE_MEMBER)

    changed = set_role_capabilities(actor, User.ROLE_MEMBER, {Capability.VIEW_ORDERS: True})

    assert changed == [Capability.VIEW_ORDERS]


@pytest.mark.parametrize("role", [User.ROLE_MANAGER, User.ROLE_PACKER, User.ROLE_MEMBER])
def test_a_non_owner_staff_member_cannot_change_a_capability(role: str) -> None:
    with pytest.raises(RoleCapabilityError):
        set_role_capabilities(
            UserFactory(is_staff=True, role=role),
            User.ROLE_PACKER,
            {Capability.STOCK_WRITE: True},
        )


def test_an_anonymous_actor_cannot_change_a_capability() -> None:
    with pytest.raises(RoleCapabilityError):
        set_role_capabilities(None, User.ROLE_PACKER, {Capability.STOCK_WRITE: True})


def test_an_inactive_owner_cannot_change_a_capability() -> None:
    with pytest.raises(RoleCapabilityError):
        set_role_capabilities(
            UserFactory(is_staff=True, role=User.ROLE_OWNER, is_active=False),
            User.ROLE_PACKER,
            {Capability.STOCK_WRITE: True},
        )


def test_the_owner_role_cannot_be_changed() -> None:
    with pytest.raises(RoleCapabilityError, match="owner"):
        set_role_capabilities(_owner(), User.ROLE_OWNER, {Capability.STOCK_WRITE: True})


def test_an_unknown_role_is_refused() -> None:
    with pytest.raises(RoleCapabilityError, match="Unknown role"):
        set_role_capabilities(_owner(), "CASHIER", {Capability.VIEW_ORDERS: True})


def test_an_unknown_capability_is_refused() -> None:
    with pytest.raises(RoleCapabilityError, match="Unknown capability"):
        set_role_capabilities(_owner(), User.ROLE_PACKER, {"FLY": True})


def test_a_non_boolean_value_is_refused() -> None:
    with pytest.raises(RoleCapabilityError, match="true or false"):
        set_role_capabilities(_owner(), User.ROLE_PACKER, {Capability.STOCK_WRITE: "yes"})


def test_a_change_writes_one_audit_row_and_moves_updated_at() -> None:
    actor = _owner()
    RoleCapability.objects.create(
        role=User.ROLE_MANAGER,
        capability=Capability.STOCK_WRITE,
        allowed=True,
    )
    row = RoleCapability.objects.get(role=User.ROLE_MANAGER, capability=Capability.STOCK_WRITE)
    before = row.updated_at

    set_role_capabilities(actor, User.ROLE_MANAGER, {Capability.STOCK_WRITE: False})

    row.refresh_from_db()
    assert row.allowed is False
    assert row.updated_at > before
    audit = AuditLog.objects.get(action=ROLE_CAPABILITY_ACTION)
    assert audit.actor_id == actor.pk
    assert audit.target_type == "accounts.rolecapability"
    assert audit.target_id == str(row.pk)
    assert audit.changes == {"allowed": [True, False]}


def test_a_new_cell_audits_the_default_as_the_old_value() -> None:
    actor = _owner()

    set_role_capabilities(actor, User.ROLE_MEMBER, {Capability.VIEW_ORDERS: True})

    audit = AuditLog.objects.get(action=ROLE_CAPABILITY_ACTION)
    assert audit.changes == {"allowed": [False, True]}


def test_one_audit_row_is_written_for_each_changed_cell() -> None:
    actor = _owner()

    changed = set_role_capabilities(
        actor,
        User.ROLE_MEMBER,
        {
            Capability.VIEW_ORDERS: True,
            Capability.STOCK_WRITE: True,
            Capability.AUDIT_READ: False,
        },
    )

    assert set(changed) == {Capability.VIEW_ORDERS, Capability.STOCK_WRITE}
    assert AuditLog.objects.filter(action=ROLE_CAPABILITY_ACTION).count() == 2


def test_only_the_posted_changed_cells_are_written() -> None:
    actor = _owner()

    set_role_capabilities(
        actor,
        User.ROLE_PACKER,
        {
            Capability.VIEW_ORDERS: True,
            Capability.START_PACKING: True,
            Capability.SEND_OUT: True,
            Capability.STOCK_WRITE: True,
        },
    )

    assert RoleCapability.objects.count() == 1
    assert RoleCapability.objects.get().capability == Capability.STOCK_WRITE


def test_an_unchanged_default_writes_no_row_and_no_audit() -> None:
    actor = _owner()

    changed = set_role_capabilities(
        actor,
        User.ROLE_PACKER,
        {Capability.VIEW_ORDERS: True, Capability.STOCK_WRITE: False},
    )

    assert changed == []
    assert RoleCapability.objects.count() == 0
    assert AuditLog.objects.count() == 0


def test_an_unchanged_stored_row_writes_nothing_and_keeps_updated_at() -> None:
    actor = _owner()
    row = RoleCapability.objects.create(
        role=User.ROLE_MANAGER,
        capability=Capability.STOCK_WRITE,
        allowed=False,
    )
    before = row.updated_at

    changed = set_role_capabilities(actor, User.ROLE_MANAGER, {Capability.STOCK_WRITE: False})

    assert changed == []
    row.refresh_from_db()
    assert row.updated_at == before
    assert AuditLog.objects.count() == 0


def test_turning_a_capability_back_to_the_default_stores_a_row_and_audits() -> None:
    actor = _owner()
    RoleCapability.objects.create(
        role=User.ROLE_MANAGER,
        capability=Capability.STOCK_WRITE,
        allowed=False,
    )

    changed = set_role_capabilities(actor, User.ROLE_MANAGER, {Capability.STOCK_WRITE: True})

    assert changed == [Capability.STOCK_WRITE]
    row = RoleCapability.objects.get()
    assert row.allowed is True
    audit = AuditLog.objects.get(action=ROLE_CAPABILITY_ACTION)
    assert audit.changes == {"allowed": [False, True]}
