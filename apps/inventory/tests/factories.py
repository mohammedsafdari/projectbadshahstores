"""Test factories for inventory models (factory_boy)."""

from decimal import Decimal
from uuid import uuid4

import factory

from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.models import InventoryMovement, StockReservation, Supplier
from tests.factories import UserFactory


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


class StockReservationFactory(factory.django.DjangoModelFactory):
    """An active fulfilment hold by default: no expiry, one new group.

    Tests that want a checkout hold pass ``kind`` and ``expires_at``; tests
    that want a finished row pass ``status`` together with the matching
    timestamp, because the database constraint requires the pair.
    """

    class Meta:
        model = StockReservation

    variant = factory.SubFactory(ProductVariantFactory)
    user = factory.SubFactory(UserFactory)
    quantity = Decimal("1.000")
    status = StockReservation.Status.ACTIVE
    kind = StockReservation.Kind.HELD_FOR_FULFILMENT
    # LazyFunction calls uuid4 for each new row, so every row gets a fresh
    # group unless a test passes one.
    checkout_group = factory.LazyFunction(uuid4)
    expires_at = None
