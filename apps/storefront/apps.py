"""Storefront app — customer-facing templates and views."""

from django.apps import AppConfig


class StorefrontConfig(AppConfig):
    name = "apps.storefront"
    label = "storefront"
    default_auto_field = "django.db.models.BigAutoField"
