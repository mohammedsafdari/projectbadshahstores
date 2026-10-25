"""Tests for the AuditLog append-only model.

Every test below touches the database.
"""

import pytest

from apps.core.models import AppendOnlyError, AuditLog
from apps.inventory.models import AppendOnlyError as InventoryAppendOnlyError

from .factories import AuditLogFactory

pytestmark = pytest.mark.django_db


def test_factory_row_has_id_created_at_and_actor_none() -> None:
    entry = AuditLogFactory()
    assert entry.id is not None
    assert entry.created_at is not None
    assert entry.actor is None


def test_changes_survive_refresh_from_db_unchanged() -> None:
    entry = AuditLogFactory()
    expected = entry.changes
    entry.refresh_from_db()
    assert entry.changes == expected


def test_str_of_an_entry() -> None:
    entry = AuditLogFactory(
        action="PRICE_CHANGE",
        target_type="catalog.productvariant",
        target_id="1",
    )
    assert str(entry) == "PRICE_CHANGE catalog.productvariant 1"


def test_saving_existing_entry_raises_append_only_error() -> None:
    entry = AuditLogFactory()
    entry.action = "OTHER"
    with pytest.raises(AppendOnlyError):
        entry.save()
    entry.refresh_from_db()
    assert entry.action != "OTHER"


def test_deleting_an_entry_raises_append_only_error() -> None:
    entry = AuditLogFactory()
    with pytest.raises(AppendOnlyError):
        entry.delete()
    entry.refresh_from_db()
    assert entry.id is not None


def test_queryset_update_raises_append_only_error() -> None:
    entry = AuditLogFactory()
    with pytest.raises(AppendOnlyError):
        AuditLog.objects.filter(pk=entry.pk).update(action="OTHER")


def test_queryset_delete_raises_append_only_error() -> None:
    entry = AuditLogFactory()
    with pytest.raises(AppendOnlyError):
        AuditLog.objects.all().delete()
    entry.refresh_from_db()
    assert entry.id is not None


def test_queryset_bulk_update_raises_append_only_error() -> None:
    entry = AuditLogFactory()
    with pytest.raises(AppendOnlyError):
        AuditLog.objects.bulk_update([entry], ["action"])


def test_deleting_the_actor_keeps_the_entry_and_sets_actor_to_none() -> None:
    from apps.accounts.models import User

    user = User.objects.create_user(phone="9876543210", password="ok")
    entry = AuditLogFactory(actor=user)
    assert entry.actor_id == user.pk

    user.delete()

    entry.refresh_from_db()
    assert entry.actor is None
    assert entry.id is not None


def test_append_only_error_is_the_same_class_from_core_and_inventory() -> None:
    assert AppendOnlyError is InventoryAppendOnlyError
