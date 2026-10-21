"""Django admin setup for the catalog app."""

from django.contrib import admin

from .models import Brand, Category, Product, ProductVariant


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
    inlines = [ProductVariantInline]


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
