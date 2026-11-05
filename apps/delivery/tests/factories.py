"""Test factories for delivery models (factory_boy)."""

import factory

from apps.delivery.models import DeliveryZone, PinCodeZone


class DeliveryZoneFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DeliveryZone

    name = factory.Sequence(lambda n: f"Zone {n}")


class PinCodeZoneFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PinCodeZone

    zone = factory.SubFactory(DeliveryZoneFactory)
    # A six-digit sequence from 520000 stays valid far past 100 rows.
    pincode = factory.Sequence(lambda n: f"{520000 + n:06d}")
