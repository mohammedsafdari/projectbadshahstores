"""Orders app — Order, OrderItem, OrderStatusLog, state machine."""

from django.apps import AppConfig


class OrdersConfig(AppConfig):
    name = "apps.orders"
    label = "orders"
    default_auto_field = "django.db.models.BigAutoField"
