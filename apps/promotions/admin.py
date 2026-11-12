"""Django admin for coupons and their redemptions (D81).

Staff manage coupons here; the role capability ``MANAGE_PROMOTIONS`` decides
who may see or edit them (D75). Every create and every change writes one audit
row through the one audit helper (D44), and a coupon is never deleted.

REVIEW CAREFULLY: money and the staff rights.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.contrib.auth.base_user import AbstractBaseUser
from django.db.models import Count, Q, QuerySet
from django.http import HttpRequest

from apps.accounts.capabilities import Capability, has_capability
from apps.core.audit import record_change, snapshot
from apps.promotions.models import Coupon, CouponRedemption

COUPON_CREATE_ACTION = "COUPON_CREATE"
COUPON_UPDATE_ACTION = "COUPON_UPDATE"

# Every editable field is audited: a coupon changes what the shop charges, so a
# change must be as traceable as a price change (D44).
AUDITED_COUPON_FIELDS: tuple[str, ...] = (
    "code",
    "description",
    "kind",
    "percent_off",
    "max_discount",
    "amount_off",
    "min_order_value",
    "total_limit",
    "per_customer_limit",
    "starts_at",
    "ends_at",
    "first_order_only",
    "is_active",
)


def _audit_actor(request: HttpRequest) -> AbstractBaseUser | None:
    """The logged-in user behind a request, or None for anything else."""
    user = getattr(request, "user", None)
    return user if isinstance(user, AbstractBaseUser) else None


class _PromotionsAdmin(admin.ModelAdmin):
    """Common capability gate for the promotions screens."""

    capability = Capability.MANAGE_PROMOTIONS

    def has_module_permission(self, request: HttpRequest) -> bool:
        return has_capability(request.user, self.capability)

    def has_view_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return has_capability(request.user, self.capability)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return has_capability(request.user, self.capability)

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return has_capability(request.user, self.capability)

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        # A coupon is never deleted: redemptions point at it with PROTECT (D81).
        return False


@admin.register(Coupon)
class CouponAdmin(_PromotionsAdmin):
    list_display = ("code", "kind", "is_active", "starts_at", "ends_at", "live_uses")
    list_filter = ("kind", "is_active", "first_order_only")
    search_fields = ("code", "description")
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request: HttpRequest) -> QuerySet[Coupon]:
        """Count the live redemptions in the list query, not once per row."""
        queryset = super().get_queryset(request)
        # ``filter=`` counts only the rows that match, so one query gives every
        # coupon its count of live uses.
        return queryset.annotate(
            live_use_count=Count(
                "redemptions",
                filter=Q(redemptions__released_at__isnull=True),
            )
        )

    @admin.display(description="Live uses")
    def live_uses(self, obj: Coupon) -> int:
        """How many unreleased redemptions still count against a limit."""
        return int(getattr(obj, "live_use_count", 0))

    def save_model(
        self,
        request: HttpRequest,
        obj: Coupon,
        form: Any,
        change: bool,
    ) -> None:
        old: dict[str, Any]
        if change:
            # Read the stored values BEFORE the save: the form has already put
            # the new values on ``obj``, so only the database has the old ones.
            stored = Coupon.objects.get(pk=obj.pk)
            old = snapshot(stored, AUDITED_COUPON_FIELDS)
        else:
            # A create has no old values, so every field reads as None before.
            old = dict.fromkeys(AUDITED_COUPON_FIELDS)
        super().save_model(request, obj, form, change)
        record_change(
            actor=_audit_actor(request),
            action=COUPON_UPDATE_ACTION if change else COUPON_CREATE_ACTION,
            target=obj,
            old=old,
            new=snapshot(obj, AUDITED_COUPON_FIELDS),
            note="Coupon (admin)",
        )


@admin.register(CouponRedemption)
class CouponRedemptionAdmin(_PromotionsAdmin):
    """A read-only history of coupon uses; nothing here may be edited."""

    list_display = (
        "created_at",
        "coupon",
        "order",
        "user",
        "discount_amount",
        "delivery_discount_amount",
        "released_at",
    )
    list_filter = ("coupon__kind", "released_at")
    search_fields = ("coupon__code", "order__order_number", "user__phone")
    list_select_related = ("coupon", "order", "user")
    list_per_page = 50

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False

    def get_readonly_fields(self, request: HttpRequest, obj: object = None) -> list[str]:
        return [field.name for field in self.model._meta.fields]
