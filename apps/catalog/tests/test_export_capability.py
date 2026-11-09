"""The CSV export actions stay capability-only (D75)."""

from __future__ import annotations

import csv
import io
from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from apps.accounts.capabilities import Capability
from apps.accounts.models import RoleCapability, User
from apps.catalog.tests.factories import ProductVariantFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:catalog_productvariant_changelist"


def _client(
    role: str | None = None,
    *,
    superuser: bool = False,
    codenames: tuple[str, ...] = (),
) -> Client:
    if superuser:
        user = UserFactory(is_staff=True, is_superuser=True, role=User.ROLE_MEMBER)
    else:
        user = UserFactory(is_staff=True, role=role or User.ROLE_MEMBER)
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    client = Client()
    client.force_login(user)
    return client


def _run_export(client: Client, pks: list[int]):
    return client.post(
        reverse(CHANGELIST),
        {"action": "export_csv", "_selected_action": pks},
    )


def test_a_manager_sees_the_export_actions() -> None:
    ProductVariantFactory()

    html = _client(User.ROLE_MANAGER).get(reverse(CHANGELIST)).content.decode()

    assert 'value="export_csv"' in html


def test_a_packer_does_not_see_the_export_actions() -> None:
    ProductVariantFactory()
    # Django's own view permission opens the list, but the capability alone
    # decides whether the export action is offered.
    client = _client(User.ROLE_PACKER, codenames=("view_productvariant",))

    response = client.get(reverse(CHANGELIST))

    assert response.status_code == 200
    assert 'value="export_csv"' not in response.content.decode()


def test_a_packer_sees_them_after_export_csv_is_granted() -> None:
    ProductVariantFactory()
    RoleCapability.objects.create(
        role=User.ROLE_PACKER,
        capability=Capability.EXPORT_CSV,
        allowed=True,
    )

    html = _client(User.ROLE_PACKER).get(reverse(CHANGELIST)).content.decode()

    assert 'value="export_csv"' in html


def test_a_direct_post_of_an_export_action_without_the_capability_is_refused() -> None:
    variant = ProductVariantFactory()
    client = _client(User.ROLE_PACKER, codenames=("view_productvariant",))

    response = _run_export(client, [variant.pk])

    assert response["Content-Type"].startswith("text/html")
    assert not response.content.startswith(b"\xef\xbb\xbf")


def test_the_export_still_contains_the_cost_price_column() -> None:
    variant = ProductVariantFactory(cost_price=Decimal("70.00"))
    client = _client(User.ROLE_MANAGER)

    response = _run_export(client, [variant.pk])
    text = response.content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))

    assert "cost_price" in rows[0]
    assert rows[0]["cost_price"] == "70.00"


def test_a_superuser_sees_the_export_actions() -> None:
    ProductVariantFactory()

    html = _client(superuser=True).get(reverse(CHANGELIST)).content.decode()

    assert 'value="export_csv"' in html
