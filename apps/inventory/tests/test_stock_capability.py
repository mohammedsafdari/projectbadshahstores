"""The STOCK_WRITE capability is the only gate for the four stock write pages.

Every user here has no Django model permission unless a test says otherwise.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from apps.accounts.capabilities import Capability
from apps.accounts.models import RoleCapability, User
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.models import InventoryMovement
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:inventory_stockitem_changelist"


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _staff(role: str) -> User:
    return UserFactory(is_staff=True, role=role)


def _url(page: str, variant: ProductVariant) -> str:
    return reverse(f"admin:inventory_stockitem_{page}", args=[variant.pk])


def _post(client: Client, page: str, variant: ProductVariant):
    return client.post(_url(page, variant), data={"quantity": "1", "reason": "Test"})


def test_a_manager_can_open_the_receive_page_by_default() -> None:
    variant = ProductVariantFactory()

    response = _client(_staff(User.ROLE_MANAGER)).get(_url("receive", variant))

    assert response.status_code == 200


def test_a_packer_cannot_open_the_receive_page() -> None:
    variant = ProductVariantFactory()

    response = _client(_staff(User.ROLE_PACKER)).get(_url("receive", variant))

    assert response.status_code == 403


def test_a_packer_cannot_post_receive() -> None:
    variant = ProductVariantFactory()

    response = _post(_client(_staff(User.ROLE_PACKER)), "receive", variant)

    assert response.status_code == 403
    assert InventoryMovement.objects.count() == 0


def test_a_packer_cannot_post_adjust() -> None:
    variant = ProductVariantFactory()

    response = _post(_client(_staff(User.ROLE_PACKER)), "adjust", variant)

    assert response.status_code == 403
    assert InventoryMovement.objects.count() == 0


def test_a_packer_cannot_post_damage() -> None:
    variant = ProductVariantFactory()

    response = _post(_client(_staff(User.ROLE_PACKER)), "damage", variant)

    assert response.status_code == 403
    assert InventoryMovement.objects.count() == 0


def test_a_packer_cannot_post_count() -> None:
    variant = ProductVariantFactory()

    response = _post(_client(_staff(User.ROLE_PACKER)), "count", variant)

    assert response.status_code == 403
    assert InventoryMovement.objects.count() == 0


def test_a_packer_can_post_receive_once_the_capability_is_granted() -> None:
    variant = ProductVariantFactory()
    RoleCapability.objects.create(
        role=User.ROLE_PACKER,
        capability=Capability.STOCK_WRITE,
        allowed=True,
    )

    response = _client(_staff(User.ROLE_PACKER)).post(
        _url("receive", variant), data={"quantity": "5"}
    )

    assert response.status_code == 302
    assert InventoryMovement.objects.count() == 1


def test_a_manager_is_refused_once_the_capability_is_removed() -> None:
    variant = ProductVariantFactory()
    RoleCapability.objects.create(
        role=User.ROLE_MANAGER,
        capability=Capability.STOCK_WRITE,
        allowed=False,
    )
    client = _client(_staff(User.ROLE_MANAGER))

    assert client.get(_url("receive", variant)).status_code == 403
    assert client.post(_url("receive", variant), data={"quantity": "5"}).status_code == 403
    assert InventoryMovement.objects.count() == 0


def test_a_manager_without_any_django_permission_can_receive() -> None:
    variant = ProductVariantFactory()
    user = _staff(User.ROLE_MANAGER)
    assert not user.user_permissions.exists()

    response = _client(user).post(_url("receive", variant), data={"quantity": "6"})

    assert response.status_code == 302
    movement = InventoryMovement.objects.get()
    assert movement.created_by_id == user.pk


def test_a_refused_post_writes_no_movement() -> None:
    # The old gate was Django's add_inventorymovement permission. A MEMBER who
    # still holds it must be refused now, because only STOCK_WRITE is checked.
    variant = ProductVariantFactory()
    member = _staff(User.ROLE_MEMBER)
    member.user_permissions.add(Permission.objects.get(codename="add_inventorymovement"))

    response = _client(member).post(
        _url("adjust", variant), data={"quantity_change": "1", "reason": "Test"}
    )

    assert response.status_code == 403
    assert InventoryMovement.objects.count() == 0


def test_the_stock_list_shows_the_action_links_only_to_a_stock_write_user() -> None:
    variant = ProductVariantFactory()
    manager_html = _client(_staff(User.ROLE_MANAGER)).get(reverse(CHANGELIST)).content.decode()

    member = _staff(User.ROLE_MEMBER)
    member.user_permissions.add(Permission.objects.get(codename="view_stockitem"))
    member_response = _client(member).get(reverse(CHANGELIST))
    member_html = member_response.content.decode()

    assert _url("receive", variant) in manager_html
    assert member_response.status_code == 200
    assert _url("receive", variant) not in member_html


def test_a_superuser_still_passes() -> None:
    variant = ProductVariantFactory()
    user = UserFactory(is_staff=True, is_superuser=True, role=User.ROLE_MEMBER)
    client = _client(user)

    assert client.get(_url("receive", variant)).status_code == 200
    assert client.post(_url("receive", variant), data={"quantity": "4"}).status_code == 302
    assert InventoryMovement.objects.count() == 1
