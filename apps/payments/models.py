"""Payments models: one row per online attempt and one per webhook (D77).

A ``Payment`` records only the online part of an order. The khata part stays in
``Order.credit_applied`` and the cash part in ``Order.amount_due_on_delivery``.
Every write happens in ``apps/payments/services.py``, never in ``save()``.
"""

from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel
from apps.orders.models import Order


class Payment(TimeStampedModel):
    """One online payment attempt for the online part of an order."""

    class Status(models.TextChoices):
        CREATED = "CREATED", "Created"
        CAPTURED = "CAPTURED", "Captured"
        FAILED = "FAILED", "Failed"
        # Refunds are written by Slice 41; the values exist so the webhook can
        # leave a late capture in a clearly unfinished state.
        REFUND_PENDING = "REFUND_PENDING", "Refund pending"
        REFUNDED = "REFUNDED", "Refunded"
        PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED", "Partially refunded"

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    provider = models.CharField(max_length=20)
    # The provider's own order id; unique, so one provider order maps to one row.
    gateway_order_id = models.CharField(max_length=64, unique=True)
    gateway_payment_id = models.CharField(max_length=64, blank=True, default="")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.CREATED,
    )
    # The method the provider reported (upi, card, netbanking, ...). It is
    # copied from the webhook, never guessed by this project.
    method = models.CharField(max_length=30, blank=True, default="")
    failure_reason = models.CharField(max_length=200, blank=True, default="")
    # A late capture on a cancelled order must be refunded by hand in Slice 41.
    needs_refund = models.BooleanField(default=False)
    captured_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    # The provider's payload, kept for support. It never contains our secrets.
    raw_response = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["order", "status"], name="payments_payment_order_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="payment_amount_gt_0",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=(
                            "CAPTURED",
                            "REFUND_PENDING",
                            "REFUNDED",
                            "PARTIALLY_REFUNDED",
                        ),
                        captured_at__isnull=False,
                    )
                    | (
                        ~models.Q(
                            status__in=(
                                "CAPTURED",
                                "REFUND_PENDING",
                                "REFUNDED",
                                "PARTIALLY_REFUNDED",
                            )
                        )
                        & models.Q(captured_at__isnull=True)
                    )
                ),
                name="payment_captured_at_matches_status",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="FAILED", failed_at__isnull=False)
                    | (~models.Q(status="FAILED") & models.Q(failed_at__isnull=True))
                ),
                name="payment_failed_at_matches_status",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider} {self.gateway_order_id} ({self.status})"


class WebhookEvent(TimeStampedModel):
    """One provider webhook, stored before it is applied (D77).

    ``event_id`` is unique, so a provider retry is applied once. The payment
    link may stay empty when the event names an order we do not know.
    """

    provider = models.CharField(max_length=20)
    event_id = models.CharField(max_length=128, unique=True)
    event_type = models.CharField(max_length=60)
    payload = models.JSONField(default=dict, blank=True)
    payment = models.ForeignKey(
        Payment,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="webhook_events",
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["provider", "event_type"], name="payments_webhook_type_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.provider} {self.event_id}"
