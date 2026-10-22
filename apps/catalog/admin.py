"""Django admin setup for the catalog app."""

from django.contrib import admin
from django.utils.html import format_html

from .models import Brand, Category, Product, ProductImage, ProductVariant


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
        "mrp",
        "selling_price",
        "hsn_code",
        "gst_rate_percent",
        "is_active",
    )


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
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


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = (
        "sku_code",
        "product",
        "unit",
        "pack_size",
        "mrp",
        "selling_price",
        "is_active",
    )
    list_filter = ("is_active", "unit", "product__is_own_brand")
    search_fields = ("sku_code", "barcode", "product__name_en", "product__name_hi")
    autocomplete_fields = ("product",)
    list_select_related = ("product",)
