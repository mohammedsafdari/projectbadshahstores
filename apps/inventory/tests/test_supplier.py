"""Tests for the Supplier model."""

import pytest
from django.db import IntegrityError

from apps.inventory.models import Supplier

from .factories import SupplierFactory

# Every test below touches the database.
pytestmark = pytest.mark.django_db


def test_supplier_str_is_name() -> None:
    supplier = SupplierFactory(name="Meena Foods")
    assert str(supplier) == "Meena Foods"


def test_supplier_defaults() -> None:
    supplier = SupplierFactory()
    assert supplier.is_active is True
    assert supplier.phone == ""
    assert supplier.notes == ""


def test_duplicate_supplier_name_is_rejected_by_database() -> None:
    SupplierFactory(name="Meena Foods")
    with pytest.raises(IntegrityError):
        SupplierFactory(name="Meena Foods")


def test_suppliers_are_ordered_by_name() -> None:
    SupplierFactory(name="Zed Supplier")
    SupplierFactory(name="Alpha Supplier")
    names = [s.name for s in Supplier.objects.all()]
    assert names == ["Alpha Supplier", "Zed Supplier"]


def test_supplier_can_be_archived() -> None:
    supplier = SupplierFactory()
    supplier.is_active = False
    supplier.save()
    supplier.refresh_from_db()
    assert supplier.is_active is False
