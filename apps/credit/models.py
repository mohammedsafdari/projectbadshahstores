"""Credit (khata) accounts and the append-only credit ledger (D67).

Money is ``Decimal`` only. ``CreditEntry.amount`` is positive when the
customer owes more (``ORDER``) and negative when they owe less (``PAYMENT``,
``CANCEL_REVERSAL``); an ``ADJUSTMENT`` may be either sign but never zero.
``CreditAccount.balance`` is a cached copy of the sum of the entries, kept in
step by the services in ``apps/credit/services.py``, never by ``save()``.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import models

from apps.core.models import AppendOnlyError, TimeStampedModel


class CreditAccount(TimeStampedModel):
    """One credit (khata) account per customer: a limit and a balance."""

    # OneToOneField is a foreign key with a unique constraint, so a customer
    # can have at most one account. PROTECT keeps a user with an account from
    # being deleted. ``related_name`` gives ``user.credit_account``.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="credit_account",
    )
    # Money is Decimal INR with two places, never float (D3, D67).
    credit_limit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    # What the customer owes now: the sum of the account's entries. A service
    # writes it under the account row lock, so it cannot drift unnoticed.
    balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    # False suspends credit: nothing new can be charged (D67 point 7).
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(credit_limit__gte=0),
                name="creditaccount_limit_gte_0",
            ),
        ]

    def __str__(self) -> str:
        return self.user.phone


# QuerySet-level update/delete/bulk_update skip save(), so they need the same
# guard as AuditLog, the inventory ledger and the order status log.
class CreditEntryQuerySet(models.QuerySet["CreditEntry"]):
    def update(self, **kwargs: Any) -> int:
        raise AppendOnlyError("Credit entries cannot be updated.")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError("Credit entries cannot be deleted.")

    def bulk_update(
        self,
        objs: Iterable[CreditEntry],
        fields: Iterable[str],
        batch_size: int | None = None,
    ) -> int:
        raise AppendOnlyError("Credit entries cannot be bulk-updated.")


class CreditEntry(TimeStampedModel):
    """One append-only credit event: who, when, how much and why (D67)."""

    class EntryType(models.TextChoices):
        ORDER = "ORDER", "Order charge"
        PAYMENT = "PAYMENT", "Payment"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"
        CANCEL_REVERSAL = "CANCEL_REVERSAL", "Cancellation reversal"

    class PaymentMode(models.TextChoices):
        CASH = "CASH", "Cash"
        UPI = "UPI", "UPI"
        BANK = "BANK", "Bank transfer"

    account = models.ForeignKey(
        CreditAccount,
        on_delete=models.PROTECT,
        related_name="entries",
    )
    entry_type = models.CharField(max_length=20, choices=EntryType.choices)
    # Positive means the customer owes more, negative means they owe less.
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    # The account balance right after this entry; the chain is proved by
    # check_credit_integrity().
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    # Set on ORDER and CANCEL_REVERSAL rows only (a named constraint below).
    order = models.ForeignKey(
        "orders.Order",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="credit_entries",
    )
    payment_mode = models.CharField(
        max_length=10,
        choices=PaymentMode.choices,
        blank=True,
        default="",
    )
    # Free text: for example a UPI transaction id or a cheque number.
    reference = models.CharField(max_length=100, blank=True, default="")
    note = models.CharField(max_length=200, blank=True, default="")
    entry_date = models.DateField()
    # Null for system actions; PROTECT keeps a user with history from deletion.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="credit_entries_created",
    )

    objects = CreditEntryQuerySet.as_manager()

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(
                fields=["account", "id"],
                name="credit_entry_account_id_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(amount=0),
                name="creditentry_amount_not_zero",
            ),
            models.CheckConstraint(
                condition=~models.Q(entry_type="ORDER") | models.Q(amount__gt=0),
                name="creditentry_order_amount_gt_0",
            ),
            models.CheckConstraint(
                condition=~models.Q(entry_type="PAYMENT") | models.Q(amount__lt=0),
                name="creditentry_payment_amount_lt_0",
            ),
            models.CheckConstraint(
                condition=~models.Q(entry_type="CANCEL_REVERSAL") | models.Q(amount__lt=0),
                name="creditentry_reversal_amount_lt_0",
            ),
            models.CheckConstraint(
                condition=~models.Q(entry_type="PAYMENT") | ~models.Q(payment_mode=""),
                name="creditentry_payment_has_mode",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(entry_type__in=["ORDER", "CANCEL_REVERSAL"])
                    | models.Q(order__isnull=False)
                ),
                name="creditentry_order_has_order",
            ),
            # A partial unique constraint: of the rows that match the
            # condition, each order may have at most one. That is the database
            # backstop for charge_order and reverse_order_charge idempotency.
            models.UniqueConstraint(
                fields=["order"],
                condition=models.Q(entry_type="ORDER"),
                name="creditentry_unique_order_charge",
            ),
            models.UniqueConstraint(
                fields=["order"],
                condition=models.Q(entry_type="CANCEL_REVERSAL"),
                name="creditentry_unique_order_reversal",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.entry_type} {self.amount}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        # ``_state.adding`` is True only for the first write of a new row.
        if not self._state.adding:
            raise AppendOnlyError("Credit entries cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError("Credit entries cannot be deleted.")
