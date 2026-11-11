"""Read-only admin screens for online payments and refunds (D77, D78).

Online payments change only through the gateway webhook, and refunds only
through the refund service. The owner can read them here; the role capability
``VIEW_PAYMENTS`` decides who may read payments and webhook events, and
``REFUND_PAYMENTS`` decides who may read refunds.
"""

from __future__ import annotations

from django.contrib import admin

from apps.accounts.capabilities import Capability, has_capability

from .models import Payment, Refund, WebhookEvent


class _ReadOnlyAdmin(admin.ModelAdmin):
    """Common read-only capability gate for the payment screens."""

    # Each screen names the one capability that opens it.
    capability = Capability.VIEW_PAYMENTS

    def has_view_permission(self, request, obj=None) -> bool:
        return has_capability(request.user, self.capability)

    def has_module_permission(self, request) -> bool:
        return has_capability(request.user, self.capability)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]


@admin.register(Payment)
class PaymentAdmin(_ReadOnlyAdmin):
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


@admin.register(Refund)
class RefundAdmin(_ReadOnlyAdmin):
    capability = Capability.REFUND_PAYMENTS

    list_display = (
        "created_at",
        "payment",
        "kind",
        "amount",
        "fee_amount",
        "status",
        "attempt_count",
        "provider_refund_id",
    )
    list_filter = ("status", "kind", "created_at")
    search_fields = (
        "payment__order__order_number",
        "payment__gateway_payment_id",
        "provider_refund_id",
    )
    list_select_related = ("payment", "payment__order")
    list_per_page = 50


@admin.register(WebhookEvent)
class WebhookEventAdmin(_ReadOnlyAdmin):
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
