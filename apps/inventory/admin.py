"""Inventory admin screens: movement history, suppliers and the Stock screen."""

from decimal import Decimal
from typing import Any, cast

from django.contrib import admin
from django.db.models import DecimalField, F, QuerySet, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpRequest

from .models import InventoryMovement, StockItem, Supplier


@admin.register(InventoryMovement)
class InventoryMovementAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "variant",
        "movement_type",
        "quantity_change",
        "reason",
        "supplier",
        "batch_number",
        "expiry_date",
        "created_by",
    )
    list_filter = ("movement_type", "supplier", "created_at")
    search_fields = (
        "variant__sku_code",
        "variant__product__name_en",
        "variant__product__name_hi",
        "reason",
        "batch_number",
    )
    # One query each for the variant, its product, the supplier and the actor
    # instead of one per row.
    list_select_related = ("variant", "variant__product", "supplier", "created_by")
    ordering = ("-created_at", "-id")
    list_per_page = 50

    # With change permission off, Django shows the entry as read-only and
    # removes the bulk "delete selected" action. Users with the view
    # permission can still open and read the entry.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "phone", "notes")
    ordering = ("name",)


class LowStockFilter(admin.SimpleListFilter):
    """Keep only variants whose on-hand is at or below their own threshold."""

    title = "stock level"
    parameter_name = "stock"

    def lookups(self, request: HttpRequest, model_admin: Any) -> list[tuple[str, str]]:
        return [("low", "Low stock")]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Any]) -> QuerySet[Any]:
        if self.value() == "low":
            return queryset.filter(
                low_stock_threshold__isnull=False,
                on_hand__lte=F("low_stock_threshold"),
            )
        return queryset


@admin.register(StockItem)
class StockItemAdmin(admin.ModelAdmin):
    list_display = (
        "sku_code",
        "product",
        "unit",
        "on_hand_display",
        "low_stock_threshold",
        "is_low_stock",
    )
    list_filter = (LowStockFilter, "is_active", "unit")
    search_fields = ("sku_code", "barcode", "product__name_en", "product__name_hi")
    list_select_related = ("product",)
    ordering = ("sku_code",)

    def get_queryset(self, request: HttpRequest) -> QuerySet[StockItem]:
        # One query for the whole list: the sum of each variant's movements
        # (ADR D42). It must match ``get_on_hand``; a test checks that.
        return (
            super()
            .get_queryset(request)
            .annotate(
                on_hand=Coalesce(
                    Sum("movements__quantity_change"),
                    Value(Decimal("0")),
                    output_field=DecimalField(max_digits=14, decimal_places=3),
                )
            )
        )

    @admin.display(description="On hand", ordering="on_hand")
    def on_hand_display(self, obj: StockItem) -> str:
        # mypy does not know about the ``on_hand`` annotation added above.
        on_hand: Decimal = cast(Any, obj).on_hand
        # ``normalize`` drops trailing zeros, so Decimal("10.000") shows as 10.
        return format(on_hand.normalize(), "f")

    @admin.display(boolean=True, description="Low stock")
    def is_low_stock(self, obj: StockItem) -> bool:
        threshold = obj.low_stock_threshold
        if threshold is None:
            return False
        on_hand: Decimal = cast(Any, obj).on_hand
        return on_hand <= threshold

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
