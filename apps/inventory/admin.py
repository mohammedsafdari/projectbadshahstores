"""Inventory admin screens: movement history, suppliers and the Stock screen."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any, cast

from django.contrib import admin, messages
from django.contrib.admin.utils import unquote
from django.core.exceptions import PermissionDenied
from django.db.models import DecimalField, F, QuerySet, Sum, Value
from django.db.models.functions import Coalesce
from django.forms import Form
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import URLPattern, path, reverse
from django.utils.html import format_html_join

from apps.accounts.models import User

from .forms import AdjustStockForm, ReceiveStockForm, RecordDamageForm
from .models import InventoryMovement, StockItem, Supplier
from .services import InventoryError, adjust_stock, receive_stock, record_damage

# Who may use the stock action pages (ADR D45, point 5). Superusers pass.
STOCK_WRITE_PERMISSION = "inventory.add_inventorymovement"

# (url name suffix, link text) for the Actions column on the Stock screen.
STOCK_ACTIONS = (("receive", "Receive"), ("adjust", "Adjust"), ("damage", "Damage"))


def _plain(value: Decimal) -> str:
    """A quantity without trailing zeros: Decimal("10.000") shows as 10."""
    return format(value.normalize(), "f")


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
        return _plain(cast(Any, obj).on_hand)

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

    def get_list_display(self, request: HttpRequest) -> list[str | Callable[[Any], str | bool]]:
        columns = list(super().get_list_display(request))
        if request.user.has_perm(STOCK_WRITE_PERMISSION):
            columns.append("stock_actions")
        return columns

    @admin.display(description="Actions")
    def stock_actions(self, obj: StockItem) -> str:
        return format_html_join(
            " | ",
            '<a href="{}">{}</a>',
            (
                (reverse(f"admin:inventory_stockitem_{name}", args=[obj.pk]), label)
                for name, label in STOCK_ACTIONS
            ),
        )

    def get_urls(self) -> list[URLPattern]:
        # Custom pages must come first: Django's own ``<id>/`` pattern would
        # otherwise swallow ``<id>/receive/``.
        custom = [
            path(
                "<path:object_id>/receive/",
                self.admin_site.admin_view(self.receive_view),
                name="inventory_stockitem_receive",
            ),
            path(
                "<path:object_id>/adjust/",
                self.admin_site.admin_view(self.adjust_view),
                name="inventory_stockitem_adjust",
            ),
            path(
                "<path:object_id>/damage/",
                self.admin_site.admin_view(self.damage_view),
                name="inventory_stockitem_damage",
            ),
        ]
        return custom + super().get_urls()

    def receive_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        return self._stock_form_view(
            request,
            object_id,
            title="Receive stock",
            form_class=ReceiveStockForm,
            apply=_apply_receive,
        )

    def adjust_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        return self._stock_form_view(
            request,
            object_id,
            title="Adjust stock",
            form_class=AdjustStockForm,
            apply=_apply_adjust,
        )

    def damage_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        return self._stock_form_view(
            request,
            object_id,
            title="Record damage",
            form_class=RecordDamageForm,
            apply=_apply_damage,
        )

    def _stock_form_view(
        self,
        request: HttpRequest,
        object_id: str,
        *,
        title: str,
        form_class: type[Form],
        apply: Callable[[StockItem, dict[str, Any], User], str],
    ) -> HttpResponse:
        """Shared page for the stock actions: show a form, call a service.

        The page never writes stock itself. ``apply`` calls a function from
        ``services.py`` and returns the success message (ADR D45, point 5).
        """
        user = request.user
        if not isinstance(user, User):
            raise PermissionDenied
        if not (self.has_view_permission(request) and user.has_perm(STOCK_WRITE_PERMISSION)):
            raise PermissionDenied

        obj = self.get_object(request, unquote(object_id))
        if obj is None:
            raise Http404("Stock item not found.")

        form = form_class(request.POST) if request.method == "POST" else form_class()
        if request.method == "POST" and form.is_valid():
            try:
                message = apply(obj, form.cleaned_data, user)
            except InventoryError as error:
                # Nothing was written: the service rolls back its own transaction.
                form.add_error(None, str(error))
            else:
                self.message_user(request, message, messages.SUCCESS)
                return HttpResponseRedirect(reverse("admin:inventory_stockitem_changelist"))

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": title,
            "submit_label": title,
            "original": obj,
            "on_hand": self.on_hand_display(obj),
            "form": form,
        }
        return TemplateResponse(request, "admin/inventory/stockitem/stock_form.html", context)


def _apply_receive(variant: StockItem, data: dict[str, Any], user: User) -> str:
    movement = receive_stock(
        variant=variant,
        quantity=data["quantity"],
        supplier=data["supplier"],
        batch_number=data["batch_number"],
        expiry_date=data["expiry_date"],
        reason=data["reason"],
        created_by=user,
    )
    return f"Received {_plain(movement.quantity_change)} of {variant.sku_code}."


def _apply_adjust(variant: StockItem, data: dict[str, Any], user: User) -> str:
    movement = adjust_stock(
        variant=variant,
        quantity_change=data["quantity_change"],
        reason=data["reason"],
        created_by=user,
    )
    return f"Adjusted {variant.sku_code} by {_plain(movement.quantity_change)}."


def _apply_damage(variant: StockItem, data: dict[str, Any], user: User) -> str:
    movement = record_damage(
        variant=variant,
        quantity=data["quantity"],
        reason=data["reason"],
        created_by=user,
    )
    return f"Recorded damage of {_plain(-movement.quantity_change)} for {variant.sku_code}."
