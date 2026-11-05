"""Core app models: small abstract base classes shared by other apps and the
AuditLog append-only audit row.

Abstract models (``abstract = True``) create no database table of their
own. Their fields are copied into every model that inherits from them.
"""

from collections.abc import Iterable
from datetime import time
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.cache import cache
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


# The one ShopSettings row always has this primary key (D58).
SHOP_SETTINGS_PK = 1

# The cache key that every save clears; get_shop_settings() reads through it.
SHOP_SETTINGS_CACHE_KEY = "core.shopsettings"


class ShopSettings(TimeStampedModel):
    """The shop-wide settings: exactly one row, always pk 1 (D58).

    Money is ``Decimal``, never float. Every text field may be blank, because
    the real GSTIN, FSSAI number and addresses come from the owner later and
    must never be guessed into a public repository.
    """

    shop_name = models.CharField(max_length=200, blank=True, default="")
    address = models.TextField(blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    gstin = models.CharField(max_length=15, blank=True, default="")
    fssai_number = models.CharField(max_length=14, blank=True, default="")
    fssai_expiry = models.DateField(null=True, blank=True)
    grievance_officer_name = models.CharField(max_length=200, blank=True, default="")
    grievance_officer_phone = models.CharField(max_length=20, blank=True, default="")
    grievance_officer_email = models.EmailField(blank=True, default="")
    minimum_order_value = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("200.00")
    )
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("30.00"))
    free_delivery_threshold = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("500.00")
    )
    cod_cap = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("2000.00"))
    # Stored as a wall-clock time and compared in settings.TIME_ZONE (D58).
    same_day_cutoff = models.TimeField(default=time(17, 0))
    reservation_minutes = models.IntegerField(default=15)
    online_stock_buffer = models.IntegerField(default=0)
    # Blank means "not configured yet": the Phase 5 number-series slice decides.
    order_number_prefix = models.CharField(max_length=20, blank=True, default="")
    invoice_number_prefix = models.CharField(max_length=20, blank=True, default="")
    is_published = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "shop settings"
        constraints = [
            # A CheckConstraint on the key makes a second row impossible.
            models.CheckConstraint(
                condition=models.Q(pk=SHOP_SETTINGS_PK),
                name="core_shopsettings_singleton",
            ),
            models.CheckConstraint(
                condition=models.Q(minimum_order_value__gte=0),
                name="core_shopsettings_minimum_order_value_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(delivery_fee__gte=0),
                name="core_shopsettings_delivery_fee_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(free_delivery_threshold__gte=0),
                name="core_shopsettings_free_delivery_threshold_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(cod_cap__gte=0),
                name="core_shopsettings_cod_cap_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(reservation_minutes__gt=0),
                name="core_shopsettings_reservation_minutes_gt_0",
            ),
            models.CheckConstraint(
                condition=models.Q(online_stock_buffer__gte=0),
                name="core_shopsettings_online_stock_buffer_gte_0",
            ),
        ]

    def __str__(self) -> str:
        return self.shop_name or "Shop settings"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Save the row and drop the cached copy (D58).

        The override holds no business logic: its only job is to make sure no
        write can leave a stale cache entry behind.
        """
        super().save(*args, **kwargs)
        cache.delete(SHOP_SETTINGS_CACHE_KEY)

    @classmethod
    def load(cls) -> "ShopSettings":
        """Return the one row, creating it with the defaults the first time."""
        # get_or_create returns a pair (object, created); we only need the object.
        row, _created = cls.objects.get_or_create(pk=SHOP_SETTINGS_PK)
        return row
