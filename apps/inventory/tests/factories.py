"""Test factories for inventory models (factory_boy)."""

import factory

from apps.inventory.models import Supplier


class SupplierFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Supplier

    name = factory.Sequence(lambda n: f"Supplier {n}")
