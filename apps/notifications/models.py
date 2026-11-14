"""Notifications app models: the log of every send attempt (D88).

The log never stores a message text, a template variable, a login code, a full
phone number or a full email address: the repository is public and a database
copy must not carry personal data or a usable code.
"""

from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel


class NotificationLog(TimeStampedModel):
    """One message the shop tried to send, and what happened to it (D88).

    ``TimeStampedModel`` adds ``created_at`` and ``updated_at``, so every row
    says when the attempt happened and when it last changed.
    """

    class Channel(models.TextChoices):
        SMS = "SMS", "SMS"
        EMAIL = "EMAIL", "Email"

    class Purpose(models.TextChoices):
        OTP = "OTP", "OTP"
        ORDER = "ORDER", "Order message"
        STAFF = "STAFF", "Staff message"
        CREDIT_REMINDER = "CREDIT_REMINDER", "Credit reminder"
        EMAIL_CODE = "EMAIL_CODE", "Email code"

    class Status(models.TextChoices):
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"
        RETRY_PENDING = "RETRY_PENDING", "Retry pending"
        GAVE_UP = "GAVE_UP", "Gave up"

    channel = models.CharField(max_length=10, choices=Channel.choices)
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    # The notice event such as ORDER_PLACED; blank for a code or a reminder.
    event = models.CharField(max_length=40, blank=True, default="")
    # SET_NULL keeps the log row when its order is deleted, so the history of a
    # send survives. related_name="+" means Order gets no reverse attribute,
    # because nothing reads an order's log rows yet.
    order = models.ForeignKey(
        "orders.Order",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # The masked contact only: "+91******1234" or "a***@example.com".
    recipient_hint = models.CharField(max_length=254, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices)
    # How many real sends this row has had; one means the first attempt.
    attempts = models.PositiveSmallIntegerField(default=1)
    # The provider's own request id when it sent one, never a phone or a code.
    provider_ref = models.CharField(max_length=100, blank=True, default="")
    error_summary = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            # The retry job reads rows by status; the admin filters by day.
            models.Index(fields=["status", "created_at"], name="notif_status_created_idx"),
            models.Index(fields=["order", "event"], name="notif_order_event_idx"),
        ]
        constraints = [
            # attempts counts real sends, so it can never be zero or negative.
            models.CheckConstraint(
                condition=models.Q(attempts__gte=1),
                name="notificationlog_attempts_gte_1",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.channel} {self.purpose} {self.status}"
