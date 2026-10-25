"""Core app models: small abstract base classes shared by other apps and the
AuditLog append-only audit row.

Abstract models (``abstract = True``) create no database table of their
own. Their fields are copied into every model that inherits from them.
"""

from collections.abc import Iterable
from typing import Any

from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    """Adds created_at / updated_at to any model that inherits from it."""

    # auto_now_add: set once, when the row is first created.
    created_at = models.DateTimeField(auto_now_add=True)
    # auto_now: refreshed on every save().
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AppendOnlyError(RuntimeError):
    """Raised when code tries to change or delete a row that is append-only."""


class AuditLogQuerySet(models.QuerySet["AuditLog"]):
    def update(self, **kwargs: Any) -> int:
        raise AppendOnlyError("Audit log entries cannot be updated.")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError("Audit log entries cannot be deleted.")

    def bulk_update(
        self,
        objs: Iterable["AuditLog"],
        fields: Iterable[str],
        batch_size: int | None = None,
    ) -> int:
        raise AppendOnlyError("Audit log entries cannot be bulk-updated.")


class AuditLog(models.Model):
    """Append-only audit rows: who changed what on which target and when.

    Stock movements are recorded in InventoryMovement (D42/D43), not here.
    This records changes to ProductVariant fields such as mrp, selling_price,
    cost_price, gst_rate_percent and is_active (D44).
    """

    # ``settings.AUTH_USER_MODEL`` is the dotted path the project chose for
    # its user class (for example "accounts.User"). Django resolves it at
    # runtime, so this FK always points at the right model even if the app
    # name changes.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    # ``null=True`` lets imports and system actions leave the actor blank.
    # ``SET_NULL`` means when the actor user is deleted, the audit row stays
    # and the FK is set to NULL instead of being deleted or raising an error.

    action = models.CharField(max_length=40)
    # Lower-case "app_label.model" so lookups are consistent.
    target_type = models.CharField(max_length=100)
    # Text, not an integer field, so any key type (int, UUID, str) fits.
    target_id = models.CharField(max_length=64)
    # ``default=dict`` passes the dict factory itself, not a shared ``{}``.
    # With ``default={}`` every new row would share the same dict object,
    # which is a classic Python gotcha. ``default=dict`` gives each row its
    # own empty dict.
    changes = models.JSONField(default=dict, blank=True)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["target_type", "target_id", "created_at"],
                name="core_audit_target_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.target_type} {self.target_id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise AppendOnlyError("Audit log entries cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError("Audit log entries cannot be deleted.")


class ArchivableModel(models.Model):
    """Adds an is_active flag so rows are archived instead of deleted."""

    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True
