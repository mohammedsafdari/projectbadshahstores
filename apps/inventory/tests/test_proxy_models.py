"""Tests for the Stock and Expiring lots proxy models."""

import pytest

from apps.catalog.models import ProductVariant
from apps.core.models import AppendOnlyError
from apps.inventory.models import ExpiringLot, InventoryMovement, StockItem
from apps.inventory.tests.factories import InventoryMovementFactory

pytestmark = pytest.mark.django_db


def test_stock_item_is_a_proxy_of_product_variant() -> None:
    assert StockItem._meta.proxy is True
    assert StockItem._meta.concrete_model is ProductVariant


def test_stock_item_returns_the_same_row() -> None:
    product_variant = InventoryMovementFactory().variant
    stock_item = StockItem.objects.get(pk=product_variant.pk)
    assert stock_item.pk == product_variant.pk
    assert stock_item.sku_code == product_variant.sku_code


def test_stock_item_reverse_relation_works() -> None:
    variant = InventoryMovementFactory().variant
    stock_item = StockItem.objects.get(pk=variant.pk)
    assert stock_item.movements.count() == 1


def test_expiring_lot_is_a_proxy_and_points_at_inventory_movement() -> None:
    assert ExpiringLot._meta.proxy is True
    assert ExpiringLot._meta.concrete_model is InventoryMovement


def test_expiring_lot_uses_the_same_table() -> None:
    InventoryMovementFactory()
    assert ExpiringLot.objects.count() == 1


def test_expiring_lot_save_raises_append_only_error() -> None:
    movement = InventoryMovementFactory()
    expiring_lot = ExpiringLot.objects.get(pk=movement.pk)
    with pytest.raises(AppendOnlyError):
        expiring_lot.save()
    expiring_lot.refresh_from_db()
    assert expiring_lot.id is not None


def test_expiring_lot_queryset_update_raises_append_only_error() -> None:
    movement = InventoryMovementFactory(reason="original reason")
    with pytest.raises(AppendOnlyError):
        ExpiringLot.objects.filter(pk=movement.pk).update(reason="x")
    movement.refresh_from_db()
    assert movement.reason == "original reason"
