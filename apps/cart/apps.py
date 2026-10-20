"""Cart app — Cart, CartItem, guest-cart merge on login."""

from django.apps import AppConfig


class CartConfig(AppConfig):
    name = "apps.cart"
    label = "cart"
    default_auto_field = "django.db.models.BigAutoField"
