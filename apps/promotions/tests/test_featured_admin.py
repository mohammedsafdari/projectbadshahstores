"""Tests for the featured product admin screens (D84).

REVIEW CAREFULLY: the twelve-row cap and the audit trail.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib import admin
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.tests.factories import ProductFactory
from apps.core.models import AuditLog
from apps.promotions.admin import (
    FEATURED_ADD_ACTION,
    FEATURED_REMOVE_ACTION,
    FEATURED_UPDATE_ACTION,
    FeaturedProductAdmin,
)
from apps.promotions.models import FeaturedProduct
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:promotions_featuredproduct_changelist"
ADD = "admin:promotions_featuredproduct_add"

# The exact sentence a thirteenth row shows.
SENTENCE = "You can feature at most 12 products. Remove one before adding another."

_request_factory = RequestFactory()


def _staff(role: str) -> User:
    return UserFactory(is_staff=True, role=role)


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _admin_for(user: User):
    request = _request_factory.get("/admin/")
    request.user = user
    return FeaturedProductAdmin(FeaturedProduct, admin.site), request


def _featured_data(product_id: int, sort_order: int = 0) -> dict[str, Any]:
    return {"product": str(product_id), "sort_order": str(sort_order)}


# ---------------------------------------------------------------------------
# Who may see and edit
# ---------------------------------------------------------------------------
def test_the_owner_sees_the_featured_list() -> None:
    FeaturedProduct.objects.create(product=ProductFactory())

    response = _client(_staff(User.ROLE_OWNER)).get(reverse(CHANGELIST))

    assert response.status_code == 200


def test_the_manager_sees_the_featured_list() -> None:
    response = _client(_staff(User.ROLE_MANAGER)).get(reverse(CHANGELIST))

    assert response.status_code == 200


@pytest.mark.parametrize("role", [User.ROLE_PACKER, User.ROLE_DELIVERY])
def test_other_roles_are_refused_the_featured_list(role: str) -> None:
    response = _client(_staff(role)).get(reverse(CHANGELIST))

    assert response.status_code == 403


def test_a_member_without_the_capability_has_no_featured_access() -> None:
    featured_admin, request = _admin_for(_staff(User.ROLE_MEMBER))

    assert featured_admin.has_module_permission(request) is False
    assert featured_admin.has_view_permission(request) is False
    assert featured_admin.has_add_permission(request) is False
    assert featured_admin.has_change_permission(request) is False
    assert featured_admin.has_delete_permission(request) is False


# ---------------------------------------------------------------------------
# The audit trail
# ---------------------------------------------------------------------------
def test_adding_writes_one_add_row() -> None:
    product = ProductFactory()
    client = _client(_staff(User.ROLE_MANAGER))

    response = client.post(reverse(ADD), _featured_data(product.pk))

    assert response.status_code == 302
    row = FeaturedProduct.objects.get()
    audit = AuditLog.objects.get(action=FEATURED_ADD_ACTION)
    # The action name is part of the audit trail, so it is checked literally.
    assert audit.action == "FEATURED_ADD"
    assert audit.target_type == "promotions.featuredproduct"
    assert audit.target_id == str(row.pk)
    assert audit.changes == {"product_id": [None, product.pk], "sort_order": [None, 0]}


def test_changing_the_order_writes_one_update_row() -> None:
    row = FeaturedProduct.objects.create(product=ProductFactory())
    client = _client(_staff(User.ROLE_OWNER))

    response = client.post(
        reverse("admin:promotions_featuredproduct_change", args=[row.pk]),
        _featured_data(row.product_id, sort_order=2),
    )

    assert response.status_code == 302
    audit = AuditLog.objects.get(action=FEATURED_UPDATE_ACTION)
    assert audit.action == "FEATURED_UPDATE"
    assert audit.changes == {"sort_order": [0, 2]}


def test_an_unchanged_save_writes_no_row() -> None:
    row = FeaturedProduct.objects.create(product=ProductFactory())
    client = _client(_staff(User.ROLE_OWNER))

    response = client.post(
        reverse("admin:promotions_featuredproduct_change", args=[row.pk]),
        _featured_data(row.product_id),
    )

    assert response.status_code == 302
    assert AuditLog.objects.filter(action=FEATURED_UPDATE_ACTION).count() == 0


def test_deleting_is_allowed_and_writes_one_remove_row() -> None:
    row = FeaturedProduct.objects.create(product=ProductFactory())
    product_id = row.product_id
    client = _client(_staff(User.ROLE_OWNER))
    delete_url = reverse("admin:promotions_featuredproduct_delete", args=[row.pk])

    # The confirmation page is shown, so a removal is allowed here.
    assert client.get(delete_url).status_code == 200

    response = client.post(delete_url, {"post": "yes"})

    assert response.status_code == 302
    assert not FeaturedProduct.objects.filter(pk=row.pk).exists()
    audit = AuditLog.objects.get(action=FEATURED_REMOVE_ACTION)
    assert audit.action == "FEATURED_REMOVE"
    assert audit.changes == {"product_id": [product_id, None], "sort_order": [0, None]}


def test_the_thirteenth_row_shows_its_sentence() -> None:
    for index in range(12):
        FeaturedProduct.objects.create(product=ProductFactory(), sort_order=index)
    client = _client(_staff(User.ROLE_OWNER))

    response = client.post(reverse(ADD), _featured_data(ProductFactory().pk))
    html = response.content.decode()

    assert response.status_code == 200
    assert SENTENCE in html
    assert FeaturedProduct.objects.count() == 12
