"""Payments models: one row per attempt, per webhook and per refund (D77, D78).

A ``Payment`` records only the online part of an order. The khata part stays in
``Order.credit_applied`` and the cash part in ``Order.amount_due_on_delivery``.
Every write happens in ``apps/payments/services.py`` or ``refunds.py``, never in
``save()``.
"""

from __future__ import annotations

from decimal import Decimal

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


class Refund(TimeStampedModel):
    """One full refund of a captured online payment (D78).

    Only a captured payment can be refunded, only the whole payment amount is
    sent back, and ``amount + fee_amount`` always equals ``payment.amount``.
    The customer pays a fee only when they cancel the order themselves; a shop
    cancellation returns every paisa.
    """

    class Kind(models.TextChoices):
        CUSTOMER_CANCEL = "CUSTOMER_CANCEL", "Customer cancelled"
        SHOP_CANCEL = "SHOP_CANCEL", "Shop cancelled"

    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested"
        SENT = "SENT", "Sent"
        PROCESSED = "PROCESSED", "Processed"
        FAILED = "FAILED", "Failed"

    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name="refunds",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.REQUESTED,
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    # The part of the payment the shop keeps. Zero for a shop cancellation.
    fee_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    # Stable for the whole refund, so a retried provider call is harmless.
    idempotency_key = models.CharField(max_length=64, unique=True)
    provider_refund_id = models.CharField(max_length=64, blank=True, default="")
    attempt_count = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=200, blank=True, default="")
    raw_response = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="payments_refund_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="refund_amount_gt_0",
            ),
            models.CheckConstraint(
                condition=models.Q(fee_amount__gte=0),
                name="refund_fee_amount_gte_0",
            ),
            # A shop cancellation never keeps a fee.
            models.CheckConstraint(
                condition=(
                    models.Q(kind="SHOP_CANCEL", fee_amount=0) | ~models.Q(kind="SHOP_CANCEL")
                ),
                name="refund_shop_cancel_has_no_fee",
            ),
            # processed_at is set exactly when the status is PROCESSED.
            models.CheckConstraint(
                condition=(
                    models.Q(status="PROCESSED", processed_at__isnull=False)
                    | (~models.Q(status="PROCESSED") & models.Q(processed_at__isnull=True))
                ),
                name="refund_processed_at_matches_status",
            ),
            # A payment has at most one refund still in flight. A failed or
            # processed refund is not retried automatically.
            models.UniqueConstraint(
                fields=["payment"],
                condition=models.Q(status__in=("REQUESTED", "SENT")),
                name="refund_open_unique_per_payment",
            ),
            models.UniqueConstraint(
                fields=["idempotency_key"],
                name="refund_idempotency_key_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.payment_id} {self.kind} {self.amount} ({self.status})"


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
