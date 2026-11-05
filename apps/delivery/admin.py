"""Django admin for delivery zones and their pin codes (D62).

Every save writes audit rows through the one audit helper (D44): one action for
the zone's own fields and one for the full list of its pin codes.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.contrib.auth.base_user import AbstractBaseUser
from django.http import HttpRequest

from apps.core.audit import record_change, snapshot
from apps.delivery.models import DeliveryZone, PinCodeZone

DELIVERY_ZONE_UPDATE_ACTION = "delivery_zone_update"
DELIVERY_PIN_CODES_UPDATE_ACTION = "delivery_pin_codes_update"

AUDITED_ZONE_FIELDS: tuple[str, ...] = ("name", "is_active")


def _audit_actor(request: HttpRequest) -> AbstractBaseUser | None:
    """The logged-in user behind a request, or None for anything else."""
    user = getattr(request, "user", None)
    return user if isinstance(user, AbstractBaseUser) else None


def _pin_codes_text(zone_pk: int) -> str:
    """The zone's pin codes as one sorted comma-separated string."""
    codes = (
        PinCodeZone.objects.filter(zone_id=zone_pk)
        .order_by("pincode")
        .values_list("pincode", flat=True)
    )
    # sorted() is a second guard in case the database collation ever differs;
    # six-digit strings sort the same way as their numbers.
    return ", ".join(sorted(codes))


class PinCodeZoneInline(admin.TabularInline):
    """The pin codes edited on one zone's page."""

    model = PinCodeZone
    extra = 0
    fields = ("pincode",)
    ordering = ("pincode",)


@admin.register(DeliveryZone)
class DeliveryZoneAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")
    inlines = [PinCodeZoneInline]

    def save_model(
        self,
        request: HttpRequest,
        obj: DeliveryZone,
        form: Any,
        change: bool,
    ) -> None:
        old = None
        if change:
            # Read the stored values BEFORE the save: the form has already put
            # the new values on ``obj``, so only the database still has the old
            # ones (the same rule as the ShopSettings admin, D58).
            stored = DeliveryZone.objects.get(pk=obj.pk)
            old = snapshot(stored, AUDITED_ZONE_FIELDS)
        super().save_model(request, obj, form, change)
        if old is not None:
            record_change(
                actor=_audit_actor(request),
                action=DELIVERY_ZONE_UPDATE_ACTION,
                target=obj,
                old=old,
                new=snapshot(obj, AUDITED_ZONE_FIELDS),
                note="Delivery zone (admin)",
            )

    def save_related(
        self,
        request: HttpRequest,
        form: Any,
        formsets: Any,
        change: bool,
    ) -> None:
        # The inline formsets are saved inside super().save_related(), so the
        # database still holds the old pin codes here. Reading them before and
        # after gives the audit row its old and new comma-list in one place.
        zone = form.instance
        old_pins = _pin_codes_text(zone.pk)
        super().save_related(request, form, formsets, change)
        new_pins = _pin_codes_text(zone.pk)
        record_change(
            actor=_audit_actor(request),
            action=DELIVERY_PIN_CODES_UPDATE_ACTION,
            target=zone,
            old={"pin_codes": old_pins},
            new={"pin_codes": new_pins},
            note="Delivery zone pin codes (admin)",
        )
