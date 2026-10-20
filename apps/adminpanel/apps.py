"""adminpanel app — Django admin customizations and actions."""

from django.apps import AppConfig


class AdminpanelConfig(AppConfig):
    name = "apps.adminpanel"
    label = "adminpanel"
    default_auto_field = "django.db.models.BigAutoField"
