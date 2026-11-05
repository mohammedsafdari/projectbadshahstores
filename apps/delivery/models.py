"""Delivery app models: zones and the pin codes inside them (D62).

A zone carries no money fields (D19 is narrowed): the delivery fee, the free
delivery threshold and the minimum order are shop-wide values in
``core.ShopSettings`` (D58).
"""

from django.core.validators import RegexValidator
from django.db import models

from apps.core.models import TimeStampedModel

# The same rule as ``apps.accounts.models.PINCODE_VALIDATOR`` (D57). It is
# copied instead of imported so ``apps.delivery`` never imports
# ``apps.accounts.models`` at import time, which would make a circle once
# accounts calls delivery for ``address_is_served``. A drift test compares the
# two validators (D62).
PINCODE_VALIDATOR = RegexValidator(
    regex=r"^[1-9][0-9]{5}$",
    message="Enter a six-digit pin code; the first digit cannot be 0.",
)


class DeliveryZone(TimeStampedModel):
    """One delivery area with a name the owner chooses."""

    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                name="delivery_zone_unique_name",
                violation_error_message="A delivery zone with this name already exists.",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class PinCodeZone(TimeStampedModel):
    """One served pin code and the zone it belongs to.

    A pin code belongs to at most one zone, so the same number cannot be served
    under two different names. The foreign key is PROTECT: a zone that still
    has pin codes cannot be deleted; staff switch it off with ``is_active``.
    """

    zone = models.ForeignKey(
        DeliveryZone,
        on_delete=models.PROTECT,
        related_name="pin_codes",
    )
    pincode = models.CharField(max_length=6, validators=[PINCODE_VALIDATOR])

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["pincode"],
                name="pincode_zone_unique_pincode",
                violation_error_message="This pin code is already in another zone.",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.pincode} ({self.zone.name})"
