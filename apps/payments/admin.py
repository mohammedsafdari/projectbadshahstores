"""Read-only admin screens for online payments (D77).

Online payments change only through the gateway webhook. The owner can read
them here, and the role capability ``VIEW_PAYMENTS`` decides who may.
"""

from __future__ import annotations

from django.contrib import admin

from apps.accounts.capabilities import Capability, has_capability

from .models import Payment, WebhookEvent


class _PaymentReadOnlyAdmin(admin.ModelAdmin):
    """Common read-only capability gate for both payment screens."""

    def has_view_permission(self, request, obj=None) -> bool:
        return has_capability(request.user, Capability.VIEW_PAYMENTS)

    def has_module_permission(self, request) -> bool:
        return has_capability(request.user, Capability.VIEW_PAYMENTS)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]


@admin.register(Payment)
class PaymentAdmin(_PaymentReadOnlyAdmin):
    list_display = (
        "created_at",
        "order",
        "provider",
        "amount",
        "status",
        "method",
        "needs_refund",
        "captured_at",
    )
    list_filter = ("status", "provider", "needs_refund", "created_at")
    search_fields = ("order__order_number", "gateway_order_id", "gateway_payment_id")
    list_select_related = ("order",)
    list_per_page = 50


@admin.register(WebhookEvent)
class WebhookEventAdmin(_PaymentReadOnlyAdmin):
    list_display = (
        "created_at",
        "provider",
        "event_type",
        "event_id",
        "payment",
        "processed_at",
        "error",
    )
    list_filter = ("provider", "event_type", "processed_at")
    search_fields = ("event_id", "payment__gateway_order_id")
    list_select_related = ("payment",)
    list_per_page = 50
