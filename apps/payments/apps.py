"""Payments app — Payment, WebhookEvent and the Razorpay gateway boundary."""

from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    name = "apps.payments"
    label = "payments"
    default_auto_field = "django.db.models.BigAutoField"
