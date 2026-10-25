"""Tests for the variant audit helpers and the ProductVariant admin save."""

from decimal import Decimal
from typing import Any, cast

import pytest
from django.contrib import admin
from django.test import RequestFactory

from apps.accounts.models import User
from apps.catalog.admin import ProductVariantAdmin
from apps.catalog.audit import (
    AUDITED_VARIANT_FIELDS,
    VARIANT_UPDATE_ACTION,
    record_variant_change,
    snapshot_variant_from_db,
)
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductFactory, ProductVariantFactory
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


def _user() -> User:
    return User.objects.create_superuser(phone="+919876543210", password="ok")


def _save_in_admin(variant: ProductVariant, user: User, *, change: bool) -> None:
    """Call the admin's save_model the way Django's change page does."""
    model_admin = ProductVariantAdmin(ProductVariant, admin.site)
    request = RequestFactory().post("/")
    request.user = user
    # The base save_model only calls obj.save(), so no real form is needed.
    model_admin.save_model(request, variant, cast(Any, None), change)


def test_audited_field_list_is_the_five_names_in_order() -> None:
    assert AUDITED_VARIANT_FIELDS == (
        "mrp",
        "selling_price",
        "cost_price",
        "gst_rate_percent",
        "is_active",
    )


def test_snapshot_from_db_returns_stored_values() -> None:
    variant = ProductVariantFactory()
    stored = snapshot_variant_from_db(variant.pk)
    assert stored["mrp"] == Decimal("100.00")
    assert stored["selling_price"] == Decimal("95.00")
    assert stored["is_active"] is True


def test_snapshot_from_db_ignores_unsaved_change_in_memory() -> None:
    variant = ProductVariantFactory()
    variant.selling_price = Decimal("1.00")  # never saved
    assert snapshot_variant_from_db(variant.pk)["selling_price"] == Decimal("95.00")


def test_record_variant_change_writes_one_row_with_actor_and_target() -> None:
    user = _user()
    variant = ProductVariantFactory()
    old = snapshot_variant_from_db(variant.pk)
    variant.selling_price = Decimal("90.00")
    row = record_variant_change(actor=user, variant=variant, old=old)
    assert row is not None
    assert row.action == VARIANT_UPDATE_ACTION
    assert row.actor == user
    assert row.target_type == "catalog.productvariant"
    assert row.target_id == str(variant.pk)
    assert row.changes == {"selling_price": ["95.00", "90.00"]}


def test_record_variant_change_returns_none_when_nothing_changed() -> None:
    variant = ProductVariantFactory()
    old = snapshot_variant_from_db(variant.pk)
    assert record_variant_change(actor=None, variant=variant, old=old) is None
    assert AuditLog.objects.count() == 0


def test_admin_save_with_price_change_saves_and_writes_one_row() -> None:
    user = _user()
    variant = ProductVariantFactory()
    variant.selling_price = Decimal("90.00")
    _save_in_admin(variant, user, change=True)
    variant.refresh_from_db()
    assert variant.selling_price == Decimal("90.00")
    row = AuditLog.objects.get()
    assert row.changes == {"selling_price": ["95.00", "90.00"]}


def test_admin_save_with_mrp_and_price_changed_writes_one_row_with_both() -> None:
    variant = ProductVariantFactory()
    variant.mrp = Decimal("120.00")
    variant.selling_price = Decimal("110.00")
    _save_in_admin(variant, _user(), change=True)
    row = AuditLog.objects.get()
    assert row.changes == {
        "mrp": ["100.00", "120.00"],
        "selling_price": ["95.00", "110.00"],
    }


def test_admin_save_with_is_active_changed_stores_bools() -> None:
    variant = ProductVariantFactory()
    variant.is_active = False
    _save_in_admin(variant, _user(), change=True)
    assert AuditLog.objects.get().changes == {"is_active": [True, False]}


def test_admin_save_with_cost_price_none_to_value_stores_none_and_string() -> None:
    variant = ProductVariantFactory(cost_price=None)
    variant.cost_price = Decimal("60.00")
    _save_in_admin(variant, _user(), change=True)
    assert AuditLog.objects.get().changes == {"cost_price": [None, "60.00"]}


def test_admin_save_with_only_non_audited_field_changed_writes_no_row() -> None:
    variant = ProductVariantFactory()
    variant.net_quantity_label = "500 g"
    _save_in_admin(variant, _user(), change=True)
    variant.refresh_from_db()
    assert variant.net_quantity_label == "500 g"
    assert AuditLog.objects.count() == 0


def test_admin_save_with_no_change_writes_no_row() -> None:
    variant = ProductVariantFactory()
    _save_in_admin(variant, _user(), change=True)
    assert AuditLog.objects.count() == 0


def test_admin_save_of_new_variant_saves_and_writes_no_row() -> None:
    variant = ProductVariantFactory.build(product=ProductFactory())
    _save_in_admin(variant, _user(), change=False)
    assert variant.pk is not None
    assert AuditLog.objects.count() == 0


def test_admin_save_row_actor_is_the_request_user() -> None:
    user = _user()
    variant = ProductVariantFactory()
    variant.selling_price = Decimal("90.00")
    _save_in_admin(variant, user, change=True)
    assert AuditLog.objects.get().actor == user
