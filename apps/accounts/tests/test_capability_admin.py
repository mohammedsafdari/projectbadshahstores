"""Tests for the owner-editable capability matrix in the admin (D75)."""

from __future__ import annotations

import re

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


def _default_post(**overrides: str) -> dict[str, str]:
    """The boxes a browser sends when every default is left as shown.

    A cleared checkbox is absent from POST, so a real save of the matrix sends
    every box that is ticked. A test that posted only one box would clear all
    the other roles by accident.
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


def _tag(html: str, name: str) -> str:
    match = re.search(rf"<input\b[^>]*name=\"{re.escape(name)}\"[^>]*>", html)
    assert match is not None, f"No input named {name}."
    return match.group(0)


def test_the_matrix_opens_for_an_owner() -> None:
    response = _owner_client().get(reverse(MATRIX_URL))

    assert response.status_code == 200
    assert b"Role capabilities" in response.content


def test_the_matrix_opens_for_a_superuser() -> None:
    client = _client(UserFactory(is_staff=True, is_superuser=True, role=User.ROLE_MEMBER))

    assert client.get(reverse(MATRIX_URL)).status_code == 200


@pytest.mark.parametrize("role", [User.ROLE_MANAGER, User.ROLE_PACKER, User.ROLE_MEMBER])
def test_the_matrix_is_refused_for_other_staff(role: str) -> None:
    client = _client(UserFactory(is_staff=True, role=role))

    assert client.get(reverse(MATRIX_URL)).status_code == 403


def test_the_matrix_is_refused_for_a_non_staff_owner() -> None:
    client = _client(UserFactory(is_staff=False, role=User.ROLE_OWNER))

    response = client.get(reverse(MATRIX_URL))

    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]


def test_the_changelist_url_opens_the_matrix() -> None:
    response = _owner_client().get(reverse("admin:accounts_rolecapability_changelist"))

    assert response.status_code == 302
    assert response["Location"] == reverse(MATRIX_URL)


def test_the_matrix_shows_the_effective_defaults() -> None:
    html = _owner_client().get(reverse(MATRIX_URL)).content.decode()

    packer_view = _tag(html, f"cap_{User.ROLE_PACKER}_{Capability.VIEW_ORDERS}")
    packer_stock = _tag(html, f"cap_{User.ROLE_PACKER}_{Capability.STOCK_WRITE}")
    member_view = _tag(html, f"cap_{User.ROLE_MEMBER}_{Capability.VIEW_ORDERS}")
    assert "checked" in packer_view
    assert "checked" not in packer_stock
    assert "checked" not in member_view


def test_the_matrix_shows_the_owner_as_always_checked_and_disabled() -> None:
    html = _owner_client().get(reverse(MATRIX_URL)).content.decode()

    owner_cell = re.search(r'data-role="OWNER"[^>]*>(.*?)</td>', html, re.DOTALL)
    assert owner_cell is not None
    assert "checked" in owner_cell.group(1)
    assert "disabled" in owner_cell.group(1)
    assert "always" in owner_cell.group(1)


def test_a_stored_override_is_shown_in_place_of_the_default() -> None:
    RoleCapability.objects.create(
        role=User.ROLE_PACKER,
        capability=Capability.VIEW_ORDERS,
        allowed=False,
    )

    html = _owner_client().get(reverse(MATRIX_URL)).content.decode()

    assert "checked" not in _tag(html, f"cap_{User.ROLE_PACKER}_{Capability.VIEW_ORDERS}")


def test_saving_a_box_creates_the_row_and_an_audit_entry() -> None:
    actor = UserFactory(is_staff=True, role=User.ROLE_OWNER)
    client = _client(actor)

    response = client.post(
        reverse(MATRIX_URL),
        _default_post(**{f"cap_{User.ROLE_MEMBER}_{Capability.VIEW_ORDERS}": "on"}),
    )

    assert response.status_code == 302
    row = RoleCapability.objects.get()
    assert row.role == User.ROLE_MEMBER
    assert row.capability == Capability.VIEW_ORDERS
    assert row.allowed is True
    assert row.updated_by_id == actor.pk
    assert AuditLog.objects.filter(action=ROLE_CAPABILITY_ACTION).count() == 1


def test_saving_an_unchanged_matrix_writes_nothing() -> None:
    client = _owner_client()

    response = client.post(
        reverse(MATRIX_URL),
        _default_post(),
        follow=True,
    )

    assert RoleCapability.objects.count() == 0
    assert AuditLog.objects.filter(action=ROLE_CAPABILITY_ACTION).count() == 0
    assert "Nothing changed." in response.content.decode()


def test_saving_a_change_shows_a_success_message() -> None:
    client = _owner_client()

    response = client.post(
        reverse(MATRIX_URL),
        _default_post(**{f"cap_{User.ROLE_MEMBER}_{Capability.AUDIT_READ}": "on"}),
        follow=True,
    )

    assert "Saved 1 capability change(s)." in response.content.decode()


def test_a_manager_cannot_save_the_matrix() -> None:
    client = _client(UserFactory(is_staff=True, role=User.ROLE_MANAGER))

    response = client.post(
        reverse(MATRIX_URL),
        {f"cap_{User.ROLE_MEMBER}_{Capability.AUDIT_READ}": "on"},
    )

    assert response.status_code == 403
    assert RoleCapability.objects.count() == 0


def test_the_owner_is_never_written_by_the_matrix() -> None:
    _owner_client().post(
        reverse(MATRIX_URL),
        _default_post(
            **{
                f"cap_{User.ROLE_OWNER}_{Capability.STOCK_WRITE}": "on",
                f"cap_{User.ROLE_MEMBER}_{Capability.STOCK_WRITE}": "on",
            }
        ),
    )

    assert list(RoleCapability.objects.values_list("role", flat=True)) == [User.ROLE_MEMBER]
