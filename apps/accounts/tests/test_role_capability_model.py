"""Tests for the RoleCapability model and its constraints (D75)."""

from __future__ import annotations

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.accounts.capabilities import Capability
from apps.accounts.models import RoleCapability, User
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _row(**values) -> RoleCapability:
    defaults = {
        "role": User.ROLE_PACKER,
        "capability": Capability.STOCK_WRITE,
        "allowed": True,
    }
    defaults.update(values)
    return RoleCapability.objects.create(**defaults)


def test_a_new_row_is_denied_by_default() -> None:
    row = RoleCapability.objects.create(
        role=User.ROLE_PACKER,
        capability=Capability.STOCK_WRITE,
    )

    assert row.allowed is False


def test_the_string_names_the_role_capability_and_state() -> None:
    row = _row()

    assert str(row) == "PACKER STOCK_WRITE: allowed"


def test_a_role_and_capability_pair_is_unique() -> None:
    _row()

    with pytest.raises(IntegrityError):
        _row()


def test_an_owner_row_is_refused() -> None:
    with pytest.raises(IntegrityError):
        _row(role=User.ROLE_OWNER)


def test_a_stored_override_keeps_its_editor() -> None:
    actor = UserFactory(is_staff=True)
    row = _row(updated_by=actor)

    assert row.updated_by_id == actor.pk


def test_updated_by_is_set_to_null_when_the_editor_is_deleted() -> None:
    actor = UserFactory(is_staff=True)
    # The audit rows of that user use SET_NULL too; deleting the editor must
    # not delete the capability row.
    row = _row(updated_by=actor)
    actor.delete()

    row.refresh_from_db()
    assert row.updated_by_id is None


def test_created_and_updated_times_are_set() -> None:
    row = _row()

    assert row.created_at is not None
    assert row.updated_at is not None


def test_saving_a_row_moves_updated_at() -> None:
    row = _row()
    before = row.updated_at
    row.allowed = False

    # Keep the clock moving on a fast machine; the point is the field is
    # included in update_fields, not that two saves differ by a second.
    row.save(update_fields=["allowed", "updated_at"])
    row.refresh_from_db()

    assert row.updated_at > before
    assert row.allowed is False
    assert timezone.is_aware(row.updated_at)
