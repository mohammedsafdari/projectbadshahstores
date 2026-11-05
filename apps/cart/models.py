"""Cart models: one saved cart per customer and its lines (D60).

The guest cart lives in the session, not here. A logged-in customer gets one
``Cart`` row, created on the first add. Money is not stored: every price is
read live from the variant until the order is placed (Slice 35).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Cart(TimeStampedModel):
    """A logged-in customer's saved cart."""

    # OneToOne: the database itself allows at most one cart per user.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cart",
    )

    def __str__(self) -> str:
        return f"Cart of {self.user_id}"


class CartItem(TimeStampedModel):
    """One variant and a decimal quantity in a saved cart."""

    cart = models.ForeignKey(
        Cart,
        on_delete=models.CASCADE,
        related_name="items",
    )
    # PROTECT: a variant that some cart still points at cannot be deleted.
    variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.PROTECT,
        related_name="cart_items",
    )
    # Decimal, never float, because loose items are sold in 1 g steps (D42).
    quantity = models.DecimalField(max_digits=12, decimal_places=3)

    class Meta:
        # Lines keep the order they were added in.
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="cart_item_quantity_gt_0",
            ),
            models.UniqueConstraint(
                fields=["cart", "variant"],
                name="cart_item_unique_variant_per_cart",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.quantity} x {self.variant_id}"
