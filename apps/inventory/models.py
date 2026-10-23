"""Inventory app models."""

from django.conf import settings
from django.db import models

from apps.core.models import ArchivableModel, TimeStampedModel


class Supplier(TimeStampedModel, ArchivableModel):
    """A supplier for purchases. Minimal record: name, phone, notes."""

    name = models.CharField(max_length=160, unique=True)
    # Plain text on purpose, no validation: landlines and STD formats vary.
    phone = models.CharField(max_length=30, blank=True)
    notes = models.TextField(blank=True)

    # Text fields use blank=True without null=True, so "no value" is always
    # the empty string, not NULL.

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class InventoryMovement(models.Model):
    """One stock event. Rows are append-only; stock is the sum of them."""

    class MovementType(models.TextChoices):
        PURCHASE_RECEIVED = "PURCHASE_RECEIVED", "Purchase received"
        IN_STORE_SALE = "IN_STORE_SALE", "In-store sale"
        SALE = "SALE", "Sale"
        RETURN = "RETURN", "Return"
        DAMAGE = "DAMAGE", "Damage"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"

    variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.PROTECT,
        related_name="movements",
    )
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    # Signed; positive adds stock, negative removes it. Counted in the
    # variant's own unit (D42). Packet variants must be whole numbers; the
    # services in the next phase check that before writing.
    quantity_change = models.DecimalField(
        max_digits=12,
        decimal_places=3,
    )
    reason = models.CharField(max_length=200, blank=True)
    # ``related_name="movements"`` gives you variant.movements and
    # supplier.movements, the reverse accessor to the related rows.
    supplier = models.ForeignKey(
        Supplier,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="movements",
    )
    batch_number = models.CharField(max_length=60, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    # Null for imports and system actions.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="inventory_movements",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # ``~Q(...)`` means NOT that expression. Inside ``Meta``, the nested
    # ``MovementType`` names are not visible, so the constraints below use the
    # plain stored strings instead.
    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(
                fields=["variant", "created_at"],
                name="inv_mov_variant_created_idx",
            ),
        ]
        constraints = [
            # Any non-zero quantity is allowed; zero is not.
            models.CheckConstraint(
                condition=~models.Q(quantity_change=0),
                name="inventory_movement_quantity_not_zero",
            ),
            models.CheckConstraint(
                # Purchases/returns add stock, sales/damage remove it,
                # adjustments may go either way.
                condition=(
                    (
                        models.Q(movement_type__in=["PURCHASE_RECEIVED", "RETURN"])
                        & models.Q(quantity_change__gt=0)
                    )
                    | (
                        models.Q(movement_type__in=["IN_STORE_SALE", "SALE", "DAMAGE"])
                        & models.Q(quantity_change__lt=0)
                    )
                    | models.Q(movement_type="ADJUSTMENT")
                ),
                name="inventory_movement_sign_matches_type",
            ),
            # An adjustment must carry a reason.
            models.CheckConstraint(
                condition=~models.Q(movement_type="ADJUSTMENT", reason=""),
                name="inventory_movement_adjustment_needs_reason",
            ),
            # Batch numbers and expiry dates are only allowed on purchases.
            models.CheckConstraint(
                condition=(
                    models.Q(movement_type="PURCHASE_RECEIVED")
                    | (models.Q(batch_number="") & models.Q(expiry_date__isnull=True))
                ),
                name="inventory_movement_batch_expiry_only_on_purchase",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_movement_type_display()} {self.quantity_change} {self.variant}"
