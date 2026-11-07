"""Orders app models: Order, OrderItem and the append-only status log (D64).

Money is ``Decimal`` only. Every foreign key is ``PROTECT`` so history cannot
be deleted by accident. Business rules (transitions, placement, cancellation)
live in ``apps/orders/services.py``, never in ``save()``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.conf import settings
from django.db import models

from apps.catalog.models import ProductVariant
from apps.core.models import AppendOnlyError, TimeStampedModel


class Order(TimeStampedModel):
    """One customer order with frozen address and money snapshots (D64)."""

    class Status(models.TextChoices):
        PLACED = "PLACED", "Placed"
        PAYMENT_PENDING = "PAYMENT_PENDING", "Payment pending"
        PAID = "PAID", "Paid"
        CONFIRMED = "CONFIRMED", "Confirmed"
        PACKING = "PACKING", "Packing"
        OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY", "Out for delivery"
        DELIVERED = "DELIVERED", "Delivered"
        CANCELLED = "CANCELLED", "Cancelled"
        PAYMENT_FAILED = "PAYMENT_FAILED", "Payment failed"
        REFUND_PENDING = "REFUND_PENDING", "Refund pending"
        REFUNDED = "REFUNDED", "Refunded"
        PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED", "Partially refunded"

    class PaymentMethod(models.TextChoices):
        COD = "COD", "Cash on delivery"
        CREDIT = "CREDIT", "Credit (khata)"

    class SubstitutionPreference(models.TextChoices):
        SUBSTITUTE = "SUBSTITUTE", "Substitute"
        REFUND = "REFUND", "Refund"
        CALL_ME = "CALL_ME", "Call me"

    order_number = models.CharField(max_length=32, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    # The reservation group this order adopted (D63). Unique, so re-submitting
    # one checkout can never create a second order (D64).
    checkout_group = models.UUIDField(unique=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PLACED,
    )
    payment_method = models.CharField(
        max_length=10,
        choices=PaymentMethod.choices,
        default=PaymentMethod.COD,
    )

    # A frozen copy of the chosen Address (D57). Later edits or archiving of
    # the address never change a placed order.
    ship_name = models.CharField(max_length=200)
    ship_phone = models.CharField(max_length=16)
    ship_line1 = models.CharField(max_length=200)
    ship_line2 = models.CharField(max_length=200, blank=True, default="")
    ship_landmark = models.CharField(max_length=200, blank=True, default="")
    ship_city = models.CharField(max_length=100)
    ship_state = models.CharField(max_length=100)
    ship_pincode = models.CharField(max_length=6)
    ship_label = models.CharField(max_length=10, blank=True, default="")

    # Money is Decimal INR with two places, never float (D3).
    item_total = models.DecimalField(max_digits=12, decimal_places=2)
    delivery_fee = models.DecimalField(max_digits=12, decimal_places=2)
    grand_total = models.DecimalField(max_digits=12, decimal_places=2)
    savings_total = models.DecimalField(max_digits=12, decimal_places=2)
    amount_due_on_delivery = models.DecimalField(max_digits=12, decimal_places=2)

    promised_delivery_date = models.DateField()
    delivery_is_same_day = models.BooleanField(default=False)
    substitution_preference = models.CharField(
        max_length=20,
        choices=SubstitutionPreference.choices,
        default=SubstitutionPreference.CALL_ME,
    )
    customer_note = models.CharField(max_length=300, blank=True, default="")
    cancel_reason = models.CharField(max_length=200, blank=True, default="")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    # The nested Status names are not visible inside Meta, so the conditions
    # below use the plain stored strings.
    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(
                fields=["user", "created_at"],
                name="orders_order_user_created_idx",
            ),
            models.Index(fields=["status"], name="orders_order_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(item_total__gte=0)
                    & models.Q(delivery_fee__gte=0)
                    & models.Q(grand_total__gte=0)
                    & models.Q(savings_total__gte=0)
                    & models.Q(amount_due_on_delivery__gte=0)
                ),
                name="order_money_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(grand_total=models.F("item_total") + models.F("delivery_fee")),
                name="order_grand_total_matches",
            ),
            # cancelled_at is set exactly when the status is CANCELLED.
            models.CheckConstraint(
                condition=(
                    models.Q(status="CANCELLED", cancelled_at__isnull=False)
                    | (~models.Q(status="CANCELLED") & models.Q(cancelled_at__isnull=True))
                ),
                name="order_cancelled_at_matches_status",
            ),
            # delivered_at is set exactly when the status is DELIVERED.
            models.CheckConstraint(
                condition=(
                    models.Q(status="DELIVERED", delivered_at__isnull=False)
                    | (~models.Q(status="DELIVERED") & models.Q(delivered_at__isnull=True))
                ),
                name="order_delivered_at_matches_status",
            ),
        ]

    def __str__(self) -> str:
        return self.order_number


class OrderItem(TimeStampedModel):
    """One frozen order line: variant, quantity and purchase-time snapshots."""

    class ItemStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        FULFILLED = "FULFILLED", "Fulfilled"
        CANCELLED = "CANCELLED", "Cancelled"

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="items",
    )
    variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.PROTECT,
        related_name="order_items",
    )

    # Snapshots copied at purchase time. The live variant is never read again
    # for display or totals (D64).
    sku_code = models.CharField(max_length=40)
    product_name = models.CharField(max_length=200)
    pack_label = models.CharField(max_length=60)
    unit = models.CharField(max_length=10, choices=ProductVariant.Unit.choices)
    hsn_code = models.CharField(max_length=8)
    gst_rate_percent = models.DecimalField(max_digits=5, decimal_places=2)
    mrp = models.DecimalField(max_digits=12, decimal_places=2)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)

    quantity_ordered = models.DecimalField(max_digits=12, decimal_places=3)
    # Packing may weigh more than ordered, so fulfilled may exceed ordered.
    quantity_fulfilled = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0,
    )
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    item_status = models.CharField(
        max_length=20,
        choices=ItemStatus.choices,
        default=ItemStatus.PENDING,
    )

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity_ordered__gt=0),
                name="orderitem_quantity_ordered_gt_0",
            ),
            # Fulfilled may exceed ordered, but never goes below zero.
            models.CheckConstraint(
                condition=models.Q(quantity_fulfilled__gte=0),
                name="orderitem_quantity_fulfilled_gte_0",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(mrp__gte=0) & models.Q(unit_price__gte=0) & models.Q(line_total__gte=0)
                ),
                name="orderitem_prices_gte_0",
            ),
            models.UniqueConstraint(
                fields=["order", "variant"],
                name="orderitem_unique_variant_per_order",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.quantity_ordered} x {self.sku_code}"


# QuerySet-level update/delete/bulk_update skip save(), so they need the same
# guard as AuditLog and the inventory ledger.
class OrderStatusLogQuerySet(models.QuerySet["OrderStatusLog"]):
    def update(self, **kwargs: Any) -> int:
        raise AppendOnlyError("Order status log entries cannot be updated.")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError("Order status log entries cannot be deleted.")

    def bulk_update(
        self,
        objs: Iterable[OrderStatusLog],
        fields: Iterable[str],
        batch_size: int | None = None,
    ) -> int:
        raise AppendOnlyError("Order status log entries cannot be bulk-updated.")


class OrderStatusLog(TimeStampedModel):
    """One append-only order status change: who, when, from and to (D10, D64)."""

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="status_logs",
    )
    # Blank only on the first row, which records the move to PLACED.
    from_status = models.CharField(max_length=20, blank=True, default="")
    to_status = models.CharField(max_length=20, choices=Order.Status.choices)
    # Null for system actions; PROTECT keeps a user with history from deletion.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="order_status_logs",
    )
    note = models.CharField(max_length=200, blank=True, default="")

    objects = OrderStatusLogQuerySet.as_manager()

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self) -> str:
        return f"{self.from_status or '-'} -> {self.to_status}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        # ``_state.adding`` is True only for the first write of a new row.
        if not self._state.adding:
            raise AppendOnlyError("Order status log entries cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError("Order status log entries cannot be deleted.")
