"""Notifications app — notification abstractions (SMS, WhatsApp, email, console)."""

from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = "apps.notifications"
    label = "notifications"
    default_auto_field = "django.db.models.BigAutoField"
