"""Read-only stock movement history in the Django admin."""

from django.contrib import admin

from .models import InventoryMovement


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
