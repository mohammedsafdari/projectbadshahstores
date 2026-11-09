"""More tests for the owner-editable capability matrix (D75)."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.capabilities import Capability, default_allowed
from apps.accounts.capability_services import ROLE_CAPABILITY_ACTION
from apps.accounts.models import RoleCapability, User
from apps.core.models import AuditLog
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

MATRIX_URL = "admin:accounts_rolecapability_matrix"
MATRIX_CHANGELIST = "admin:accounts_rolecapability_changelist"


def _default_post(**overrides: str) -> dict[str, str]:
    """The boxes a browser sends when every default is left as shown.

    A cleared checkbox is absent from POST, so the helper sends every box the
    defaults tick. Posting one box alone would clear the other roles.
    """
    data: dict[str, str] = {}
    for role in (User.ROLE_MANAGER, User.ROLE_PACKER, User.ROLE_DELIVERY, User.ROLE_MEMBER):
        for capability in Capability.values:
            if default_allowed(role, capability):
                data[f"cap_{role}_{capability}"] = "on"
    data.update(overrides)
    return data


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _owner_client() -> Client:
    return _client(UserFactory(is_staff=True, role=User.ROLE_OWNER))


def test_the_matrix_lists_all_ten_capabilities_with_labels() -> None:
    html = _owner_client().get(reverse(MATRIX_URL)).content.decode()

    for capability in Capability.values:
        assert f'name="cap_{User.ROLE_MANAGER}_{capability}"' in html
        assert Capability(capability).label in html


def test_the_matrix_post_requires_csrf() -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(UserFactory(is_staff=True, role=User.ROLE_OWNER))

    response = client.post(
        reverse(MATRIX_URL),
        _default_post(**{f"cap_{User.ROLE_MEMBER}_{Capability.AUDIT_READ}": "on"}),
    )

    assert response.status_code == 403
    assert RoleCapability.objects.count() == 0


def test_the_matrix_post_with_an_unknown_capability_changes_nothing() -> None:
    client = _owner_client()

    response = client.post(
        reverse(MATRIX_URL),
        _default_post(**{f"cap_{User.ROLE_MANAGER}_NOT_A_CAPABILITY": "on"}),
    )

    assert response.status_code == 302
    assert RoleCapability.objects.count() == 0
    assert not AuditLog.objects.filter(action=ROLE_CAPABILITY_ACTION).exists()


def test_the_matrix_menu_entry_is_hidden_from_a_manager() -> None:
    owner_html = _owner_client().get(reverse("admin:index")).content.decode()
    manager_html = (
        _client(UserFactory(is_staff=True, role=User.ROLE_MANAGER))
        .get(reverse("admin:index"))
        .content.decode()
    )

    assert reverse(MATRIX_CHANGELIST) in owner_html
    assert reverse(MATRIX_CHANGELIST) not in manager_html


def test_a_packer_cannot_open_the_matrix() -> None:
    response = _client(UserFactory(is_staff=True, role=User.ROLE_PACKER)).get(reverse(MATRIX_URL))

    assert response.status_code == 403


def test_an_anonymous_visitor_is_sent_to_login_from_the_matrix() -> None:
    response = Client().get(reverse(MATRIX_URL))

    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]
