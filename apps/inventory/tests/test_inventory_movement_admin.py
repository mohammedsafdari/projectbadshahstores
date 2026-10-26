"""Tests for the read-only InventoryMovement admin."""

from decimal import Decimal

import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.accounts.models import User
from apps.inventory.admin import InventoryMovementAdmin
from apps.inventory.models import InventoryMovement
from apps.inventory.tests.factories import InventoryMovementFactory

pytestmark = pytest.mark.django_db


def _admin() -> InventoryMovementAdmin:
    return InventoryMovementAdmin(InventoryMovement, admin.site)


def _superuser_client() -> Client:
    user = User.objects.create_superuser(phone="+919876543210", password="ok")
    client = Client()
    client.force_login(user)
    return client


def _request_with_user(user: User) -> object:
    request = RequestFactory().get("/admin/inventory/inventorymovement/")
    request.user = user
    return request


def test_has_add_permission_false_for_superuser() -> None:
    admin_instance = _admin()
    user = User.objects.create_superuser(phone="+919876543211", password="ok")
    request = _request_with_user(user)
    assert admin_instance.has_add_permission(request) is False


def test_has_change_permission_false_for_superuser() -> None:
    admin_instance = _admin()
    user = User.objects.create_superuser(phone="+919876543212", password="ok")
    request = _request_with_user(user)
    assert admin_instance.has_change_permission(request) is False


def test_has_delete_permission_false_for_superuser() -> None:
    admin_instance = _admin()
    user = User.objects.create_superuser(phone="+919876543213", password="ok")
    request = _request_with_user(user)
    assert admin_instance.has_delete_permission(request) is False


def test_changelist_200_for_superuser_contains_variant_sku() -> None:
    movement = InventoryMovementFactory()
    client = _superuser_client()
    response = client.get(reverse("admin:inventory_inventorymovement_changelist"))
    assert response.status_code == 200
    content = response.content.decode("utf-8", "replace")
    assert movement.variant.sku_code in content


def test_staff_without_permissions_gets_403_on_changelist() -> None:
    user = User.objects.create_user(phone="+919876543220", password="ok", is_staff=True)
    client = Client()
    client.force_login(user)
    response = client.get(reverse("admin:inventory_inventorymovement_changelist"))
    assert response.status_code == 403


def test_staff_with_only_view_permission_can_read_changelist() -> None:
    user = User.objects.create_user(phone="+919876543230", password="ok", is_staff=True)
    view_perm = Permission.objects.get(codename="view_inventorymovement")
    user.user_permissions.add(view_perm)
    client = Client()
    client.force_login(user)
    response = client.get(reverse("admin:inventory_inventorymovement_changelist"))
    assert response.status_code == 200


def test_search_by_variant_sku_shows_that_variant_only() -> None:
    variant_a = InventoryMovementFactory().variant
    sku_a = variant_a.sku_code
    inventory_movement_b = InventoryMovementFactory()
    sku_b = inventory_movement_b.variant.sku_code
    client = _superuser_client()
    response = client.get(
        reverse("admin:inventory_inventorymovement_changelist"), data={"q": sku_a}
    )
    assert response.status_code == 200
    content = response.content.decode("utf-8", "replace")
    assert sku_a in content
    assert sku_b not in content


def test_filter_by_movement_type_damage_shows_damage_only() -> None:
    variant = InventoryMovementFactory().variant
    damage = InventoryMovementFactory(
        variant=variant,
        movement_type=InventoryMovement.MovementType.DAMAGE,
        quantity_change=-Decimal("5.000"),
    )
    purchase = InventoryMovementFactory(variant=variant)
    client = _superuser_client()
    response = client.get(
        reverse("admin:inventory_inventorymovement_changelist"),
        data={"movement_type": "DAMAGE"},
    )
    assert response.status_code == 200
    assert damage.id in {row.id for row in response.context["cl"].result_list}
    assert purchase.id not in {row.id for row in response.context["cl"].result_list}


def test_changelist_lists_newest_movement_first() -> None:
    first = InventoryMovementFactory()
    second = InventoryMovementFactory()
    client = _superuser_client()
    response = client.get(reverse("admin:inventory_inventorymovement_changelist"))
    assert response.status_code == 200
    rows = list(response.context["cl"].result_list)
    assert rows[0].id == second.id
    assert rows[1].id == first.id


def test_add_page_403_for_superuser() -> None:
    client = _superuser_client()
    response = client.get(reverse("admin:inventory_inventorymovement_add"))
    assert response.status_code == 403


def test_post_to_change_page_403_and_reason_unchanged() -> None:
    movement = InventoryMovementFactory(reason="original reason")
    client = _superuser_client()
    response = client.post(
        reverse("admin:inventory_inventorymovement_change", args=[movement.pk]),
        data={"reason": "tampered reason"},
    )
    assert response.status_code == 403
    movement.refresh_from_db()
    assert movement.reason == "original reason"
