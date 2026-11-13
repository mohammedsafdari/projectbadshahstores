"""Django admin for coupons, banners and featured products (D81, D84).

Staff manage promotions here; the role capability ``MANAGE_PROMOTIONS``
decides who may see or edit them (D75). Every create and every change writes
one audit row through the one audit helper (D44), and a coupon or a banner is
never deleted.

REVIEW CAREFULLY: money and the staff rights.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.contrib.auth.base_user import AbstractBaseUser
from django.db.models import Count, Q, QuerySet
from django.http import HttpRequest
from django.utils.html import format_html
from django.utils.safestring import SafeString

from apps.accounts.capabilities import Capability, has_capability
from apps.core.audit import record_change, snapshot
from apps.promotions.models import Banner, Coupon, CouponRedemption, FeaturedProduct

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
        # A coupon and a banner are never deleted: a coupon is switched off and
        # redemptions point at it with PROTECT (D81); a banner is switched off.
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


# --- the home page: banners and featured products (D84) ----------------------

BANNER_CREATE_ACTION = "BANNER_CREATE"
BANNER_UPDATE_ACTION = "BANNER_UPDATE"
FEATURED_ADD_ACTION = "FEATURED_ADD"
FEATURED_UPDATE_ACTION = "FEATURED_UPDATE"
FEATURED_REMOVE_ACTION = "FEATURED_REMOVE"

# The image columns are left out: they hold file objects, and the audit helper
# stores plain JSON values only. Every other editable field is audited.
AUDITED_BANNER_FIELDS: tuple[str, ...] = (
    "title",
    "alt_text",
    "link_url",
    "sort_order",
    "starts_at",
    "ends_at",
    "is_active",
)

# ``product_id``, not ``product``: the audit helper stores plain values, and a
# Product object is not one of them.
AUDITED_FEATURED_FIELDS: tuple[str, ...] = ("product_id", "sort_order")


@admin.register(Banner)
class BannerAdmin(_PromotionsAdmin):
    """Create and edit home page banners; a banner is switched off, not deleted."""

    list_display = ("title", "is_active", "starts_at", "ends_at", "sort_order", "preview")
    list_filter = ("is_active",)
    search_fields = ("title", "alt_text")
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Preview")
    def preview(self, obj: Banner) -> SafeString:
        """A small image for the list; the values are escaped by format_html."""
        if not obj.image:
            return format_html("")
        # The WebP copy when the conversion worked, the original when it did not.
        source = obj.display.url if obj.display else obj.image.url
        return format_html(
            '<img src="{}" alt="{}" width="96" height="48" style="object-fit: cover;">',
            source,
            obj.alt_text,
        )

    def save_model(
        self,
        request: HttpRequest,
        obj: Banner,
        form: Any,
        change: bool,
    ) -> None:
        old: dict[str, Any]
        if change:
            # Read the stored values BEFORE the save: the form has already put
            # the new values on ``obj``, so only the database has the old ones.
            stored = Banner.objects.get(pk=obj.pk)
            old = snapshot(stored, AUDITED_BANNER_FIELDS)
        else:
            # A create has no old values, so every field reads as None before.
            old = dict.fromkeys(AUDITED_BANNER_FIELDS)
        super().save_model(request, obj, form, change)
        record_change(
            actor=_audit_actor(request),
            action=BANNER_UPDATE_ACTION if change else BANNER_CREATE_ACTION,
            target=obj,
            old=old,
            new=snapshot(obj, AUDITED_BANNER_FIELDS),
            note="Banner (admin)",
        )


@admin.register(FeaturedProduct)
class FeaturedProductAdmin(_PromotionsAdmin):
    """Hand-order the products the home page features; rows may be removed."""

    list_display = ("product", "sort_order", "created_at")
    list_select_related = ("product",)
    # A raw id box, not a list of the whole catalogue (D84).
    raw_id_fields = ("product",)
    readonly_fields = ("created_at", "updated_at")

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        # Removing a row only takes the product off the home page, so it is allowed.
        return has_capability(request.user, self.capability)

    def save_model(
        self,
        request: HttpRequest,
        obj: FeaturedProduct,
        form: Any,
        change: bool,
    ) -> None:
        old: dict[str, Any]
        if change:
            stored = FeaturedProduct.objects.get(pk=obj.pk)
            old = snapshot(stored, AUDITED_FEATURED_FIELDS)
        else:
            old = dict.fromkeys(AUDITED_FEATURED_FIELDS)
        super().save_model(request, obj, form, change)
        record_change(
            actor=_audit_actor(request),
            action=FEATURED_UPDATE_ACTION if change else FEATURED_ADD_ACTION,
            target=obj,
            old=old,
            new=snapshot(obj, AUDITED_FEATURED_FIELDS),
            note="Featured product (admin)",
        )

    def delete_model(self, request: HttpRequest, obj: FeaturedProduct) -> None:
        """Audit a removal first: ``delete()`` clears the primary key."""
        record_change(
            actor=_audit_actor(request),
            action=FEATURED_REMOVE_ACTION,
            target=obj,
            old=snapshot(obj, AUDITED_FEATURED_FIELDS),
            new=dict.fromkeys(AUDITED_FEATURED_FIELDS),
            note="Featured product (admin)",
        )
        super().delete_model(request, obj)

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[FeaturedProduct]) -> None:
        """Send the "delete selected" action through delete_model, one row each."""
        for obj in queryset:
            self.delete_model(request, obj)
