"""AUDIT_READ is the only gate for the read-only audit log (D75 amendment).

Every staff user here has no Django model permission unless a test says so.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.capabilities import Capability
from apps.accounts.models import RoleCapability, User
from apps.core.tests.factories import AuditLogFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

LIST_URL = "admin:core_auditlog_changelist"


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _staff(role: str) -> User:
    return UserFactory(is_staff=True, role=role)


def test_an_owner_can_read_the_audit_log() -> None:
    AuditLogFactory()

    response = _client(_staff(User.ROLE_OWNER)).get(reverse(LIST_URL))

    assert response.status_code == 200


def test_a_manager_can_read_the_audit_log_by_default() -> None:
    AuditLogFactory()

    response = _client(_staff(User.ROLE_MANAGER)).get(reverse(LIST_URL))

    assert response.status_code == 200


def test_a_packer_cannot_read_the_audit_log() -> None:
    AuditLogFactory()

    response = _client(_staff(User.ROLE_PACKER)).get(reverse(LIST_URL))

    assert response.status_code == 403


def test_a_packer_can_read_it_after_audit_read_is_granted() -> None:
    AuditLogFactory()
    RoleCapability.objects.create(
        role=User.ROLE_PACKER,
        capability=Capability.AUDIT_READ,
        allowed=True,
    )

    response = _client(_staff(User.ROLE_PACKER)).get(reverse(LIST_URL))

    assert response.status_code == 200


def test_a_manager_without_any_django_permission_can_read_it() -> None:
    entry = AuditLogFactory()
    user = _staff(User.ROLE_MANAGER)
    assert not user.user_permissions.exists()
    client = _client(user)

    assert client.get(reverse(LIST_URL)).status_code == 200
    assert client.get(reverse("admin:core_auditlog_change", args=[entry.pk])).status_code == 200


def test_the_audit_log_stays_read_only_for_everyone() -> None:
    entry = AuditLogFactory(note="original note")
    client = _client(_staff(User.ROLE_OWNER))

    assert client.get(reverse("admin:core_auditlog_add")).status_code == 403
    assert client.get(reverse("admin:core_auditlog_delete", args=[entry.pk])).status_code == 403
    assert (
        client.post(
            reverse("admin:core_auditlog_change", args=[entry.pk]),
            data={"note": "tampered note"},
        ).status_code
        == 403
    )

    entry.refresh_from_db()
    assert entry.note == "original note"


def test_the_audit_log_menu_entry_follows_the_capability() -> None:
    manager_html = _client(_staff(User.ROLE_MANAGER)).get(reverse("admin:index")).content.decode()
    packer_html = _client(_staff(User.ROLE_PACKER)).get(reverse("admin:index")).content.decode()

    assert reverse(LIST_URL) in manager_html
    assert reverse(LIST_URL) not in packer_html
