"""Delivery app — DeliveryZone, PinCodeZone, DeliverySlotDefinition, DeliveryAssignment."""

from django.apps import AppConfig


class DeliveryConfig(AppConfig):
    name = "apps.delivery"
    label = "delivery"
    default_auto_field = "django.db.models.BigAutoField"
