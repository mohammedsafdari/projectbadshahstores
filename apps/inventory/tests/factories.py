"""Test factories for inventory models (factory_boy)."""

from decimal import Decimal

import factory

from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.models import InventoryMovement, Supplier


class SupplierFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Supplier

    name = factory.Sequence(lambda n: f"Supplier {n}")


class InventoryMovementFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = InventoryMovement

    variant = factory.SubFactory(ProductVariantFactory)
    movement_type = InventoryMovement.MovementType.PURCHASE_RECEIVED
    quantity_change = Decimal("10.000")
