"""Tests for the Supplier admin."""

import pytest
from django.contrib import admin
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.accounts.models import User
from apps.inventory.admin import SupplierAdmin
from apps.inventory.models import Supplier
from apps.inventory.tests.factories import InventoryMovementFactory, SupplierFactory

pytestmark = pytest.mark.django_db


def _admin() -> SupplierAdmin:
    return SupplierAdmin(Supplier, admin.site)


def _superuser_client() -> Client:
    user = User.objects.create_superuser(phone="+919876543210", password="ok")
    client = Client()
    client.force_login(user)
    return client


def _request_with_user(user: User) -> object:
    request = RequestFactory().get("/admin/inventory/supplier/")
    request.user = user
    return request


def _staff_client_without_permissions() -> Client:
    user = User.objects.create_user(phone="+919876543220", password="ok", is_staff=True)
    client = Client()
    client.force_login(user)
    return client


def test_changelist_200_for_superuser_contains_supplier_name() -> None:
    supplier = SupplierFactory()
    client = _superuser_client()
    response = client.get(reverse("admin:inventory_supplier_changelist"))
    assert response.status_code == 200
    content = response.content.decode("utf-8", "replace")
    assert supplier.name in content


def test_staff_without_permissions_gets_403_on_changelist() -> None:
    client = _staff_client_without_permissions()
    response = client.get(reverse("admin:inventory_supplier_changelist"))
    assert response.status_code == 403


def test_search_by_supplier_name_shows_that_supplier_only() -> None:
    supplier_a = SupplierFactory(name="Alpha Supplier")
    supplier_b = SupplierFactory(name="Beta Supplier")
    client = _superuser_client()
    response = client.get(
        reverse("admin:inventory_supplier_changelist"), data={"q": supplier_a.name}
    )
    assert response.status_code == 200
    content = response.content.decode("utf-8", "replace")
    assert supplier_a.name in content
    assert supplier_b.name not in content


def test_filter_by_is_active_false_shows_archived_only() -> None:
    active = SupplierFactory()
    archived = SupplierFactory(is_active=False)
    client = _superuser_client()
    response = client.get(
        reverse("admin:inventory_supplier_changelist"),
        data={"is_active__exact": "0"},
    )
    assert response.status_code == 200
    rows = list(response.context["cl"].result_list)
    assert active.id not in {row.id for row in rows}
    assert archived.id in {row.id for row in rows}


def test_post_to_add_page_creates_supplier() -> None:
    client = _superuser_client()
    response = client.post(
        reverse("admin:inventory_supplier_add"),
        data={"name": "New Supplier", "phone": "+919876500000", "notes": ""},
        follow=False,
    )
    assert response.status_code == 302
    assert Supplier.objects.filter(name="New Supplier").exists()


def test_post_delete_on_protected_supplier_keeps_it() -> None:
    supplier = SupplierFactory()
    InventoryMovementFactory(supplier=supplier)
    client = _superuser_client()
    client.post(
        reverse("admin:inventory_supplier_delete", args=[supplier.pk]),
        data={"post": "yes"},
    )
    # Deleting a protected object is not confirmed by a 302; Django shows the
    # protected error page and leaves the supplier in the database.
    assert Supplier.objects.filter(pk=supplier.pk).exists()
