"""Reports app — read-only staff reports and the private cost price upload."""

from django.apps import AppConfig


class ReportsConfig(AppConfig):
    name = "apps.reports"
    label = "reports"
    default_auto_field = "django.db.models.BigAutoField"
