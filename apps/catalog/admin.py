"""Django admin setup for the catalog app."""

from typing import Any

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from django.utils.html import format_html

from apps.catalog.audit import (
    audit_actor,
    record_variant_change,
    record_variant_formset_changes,
    snapshot_variant_from_db,
    snapshot_variants_before_formset_save,
)

from .csv_export import variants_for_export, write_variants_csv
from .models import Brand, Category, Product, ProductImage, ProductVariant


def _csv_download(variants: QuerySet[ProductVariant]) -> HttpResponse:
    """Build the CSV file download for the given variants (Slice 19)."""
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    filename = f"variants-{timezone.localdate():%Y-%m-%d}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    # The file holds cost prices, so browsers and proxies must not keep a copy.
    response["Cache-Control"] = "no-store"
    # An HttpResponse works like a file: write() adds text to the download.
    write_variants_csv(variants_for_export(variants), response)
    return response


class VariantCsvExportPermissionMixin:
    """Who may run the CSV export actions.

    The file shows what the variant pages show (cost price included), so the
    rule is the same: the user needs the view permission for variants. Django
    finds this method by name: an action declared with ``permissions=["export"]``
    is offered only when ``has_export_permission`` returns True.
    """

    def has_export_permission(self, request: HttpRequest) -> bool:
        return request.user.has_perm("catalog.view_productvariant")


class ProductImageInline(admin.TabularInline):
    """Photos for a product, edited on the product page."""

    # extra = 0: no blank "add another" rows on the product page.
    model = ProductImage
    extra = 0
    # Fields shown for each inline row, in this order.
    fields = ("image", "preview", "alt_text", "sort_order")
    # readonly_fields is the Django admin idiom for "show this, but do not
    # let staff edit it". Here it holds the computed thumbnail.
    readonly_fields = ("preview",)

    @admin.display(description="Preview")
    def preview(self, obj):
        # @admin.display sets the column header for this method in the admin.
        if not obj.pk or not obj.image:
            # Not saved yet: there is no file, so no thumbnail to show.
            return "-"
        # format_html escapes every value placed into {} (the URL and the alt
        # text), so a malicious file name or alt text cannot inject HTML.
        # The outer quotes are single so the double quotes inside are fine.
        return format_html(
            '<img src="{}" alt="{}" style="height:80px;">',
            obj.image.url,
            obj.alt_text,
        )


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    # Fills the slug box automatically while typing the name.
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "parent", "slug", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


class ProductVariantInline(admin.TabularInline):
    """Variants (pack sizes) edited on the product page."""

    model = ProductVariant
    extra = 0
    fields = (
        "sku_code",
        "barcode",
        "unit",
        "pack_size",
        "is_sold_loose",
        "quantity_step",
        "low_stock_threshold",
        "mrp",
        "selling_price",
        "hsn_code",
        "gst_rate_percent",
        "is_active",
    )


@admin.register(Product)
class ProductAdmin(VariantCsvExportPermissionMixin, admin.ModelAdmin):
    list_display = (
        "name_en",
        "name_hi",
        "brand",
        "category",
        "is_own_brand",
        "diet_type",
        "is_active",
    )
    list_filter = ("is_own_brand", "diet_type", "is_active", "brand", "category")
    search_fields = (
        "name_en",
        "name_hi",
        "slug",
        "search_keywords",
        "variants__sku_code",
        "variants__barcode",
    )
    prepopulated_fields = {"slug": ("name_en",)}
    autocomplete_fields = ("brand", "category")
    list_select_related = ("brand", "category")
    # Inlines appear on the product change page, below the product fields.
    # ProductImageInline comes after ProductVariantInline so variants stay first.
    inlines = [ProductVariantInline, ProductImageInline]
    actions = ["export_variants_csv"]

    @admin.action(
        description="Export all variants of the selected products to CSV",
        permissions=["export"],
    )
    def export_variants_csv(
        self, request: HttpRequest, queryset: QuerySet[Product]
    ) -> HttpResponse:
        # The selected rows are products; the file has one row per variant.
        return _csv_download(ProductVariant.objects.filter(product__in=queryset))

    # save_formset runs once for each inline (variants, then images). Only the
    # variants formset is audited. The old values are read from the database
    # before the save, because the forms have already changed their instances.
    # Django's admin wraps the whole save in one transaction, so the variant
    # edits and their audit rows are saved together or not at all.
    def save_formset(self, request: HttpRequest, form: Any, formset: Any, change: bool) -> None:
        if formset.model is not ProductVariant:
            super().save_formset(request, form, formset, change)
            return
        old_by_pk = snapshot_variants_before_formset_save(formset)
        super().save_formset(request, form, formset, change)
        record_variant_formset_changes(
            actor=audit_actor(request), formset=formset, old_by_pk=old_by_pk
        )


@admin.register(ProductVariant)
class ProductVariantAdmin(VariantCsvExportPermissionMixin, admin.ModelAdmin):
    list_display = (
        "sku_code",
        "product",
        "unit",
        "pack_size",
        "is_sold_loose",
        "mrp",
        "selling_price",
        "is_active",
    )
    list_filter = ("is_active", "is_sold_loose", "unit", "product__is_own_brand")
    search_fields = ("sku_code", "barcode", "product__name_en", "product__name_hi")
    autocomplete_fields = ("product",)
    list_select_related = ("product",)
    actions = ["export_csv"]

    @admin.action(description="Export the selected variants to CSV", permissions=["export"])
    def export_csv(self, request: HttpRequest, queryset: QuerySet[ProductVariant]) -> HttpResponse:
        return _csv_download(queryset)

    # By the time save_model runs, the form has already put the new values on
    # ``obj``, so the old values must be read from the database first. Django's
    # admin wraps the whole save in one transaction, so the variant and its
    # audit row are saved together or not at all.
    def save_model(
        self, request: HttpRequest, obj: ProductVariant, form: Any, change: bool
    ) -> None:
        old = snapshot_variant_from_db(obj.pk) if change else None
        super().save_model(request, obj, form, change)
        if old is not None:
            record_variant_change(actor=audit_actor(request), variant=obj, old=old)
