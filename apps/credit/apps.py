"""Credit app configuration."""

from django.apps import AppConfig


class CreditConfig(AppConfig):
    name = "apps.credit"
    label = "credit"
    default_auto_field = "django.db.models.BigAutoField"
