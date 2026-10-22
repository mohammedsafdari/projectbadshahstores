"""Test factories for catalog models (factory_boy)."""

from decimal import Decimal

import factory

from apps.catalog.models import Brand, Category, Product, ProductImage, ProductVariant


class BrandFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Brand

    # Sequence gives each new object a unique number n, so unique
    # constraints on name and slug never collide.
    name = factory.Sequence(lambda n: f"Brand {n}")
    slug = factory.Sequence(lambda n: f"brand-{n}")


class CategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Category

    name = factory.Sequence(lambda n: f"Category {n}")
    slug = factory.Sequence(lambda n: f"category-{n}")
    parent = None


class ProductFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Product

    name_en = factory.Sequence(lambda n: f"Product {n}")
    slug = factory.Sequence(lambda n: f"product-{n}")
    # SubFactory creates a related Brand / Category automatically.
    brand = factory.SubFactory(BrandFactory)
    category = factory.SubFactory(CategoryFactory)


class ProductVariantFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ProductVariant

    product = factory.SubFactory(ProductFactory)
    sku_code = factory.Sequence(lambda n: f"SKU-{n:05d}")
    barcode = None
    unit = ProductVariant.Unit.KG
    pack_size = Decimal("1.000")
    hsn_code = "1101"
    gst_rate_percent = Decimal("5.00")
    mrp = Decimal("100.00")
    selling_price = Decimal("95.00")


class ProductImageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ProductImage

    product = factory.SubFactory(ProductFactory)
    # factory.django.ImageField builds a tiny real image for tests.
    image = factory.django.ImageField(height=20, width=20)
    alt_text = ""
    sort_order = 0
