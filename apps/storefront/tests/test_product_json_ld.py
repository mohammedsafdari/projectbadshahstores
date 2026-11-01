"""Tests for product detail JSON-LD (Slice 25)."""

from __future__ import annotations

import json

import pytest

from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.storefront.product_detail import get_product_detail


@pytest.mark.django_db
def test_json_ld_is_valid_json_with_product_type() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True, description="desc")
    ProductVariantFactory(
        product=product, is_active=True, selling_price=10, mrp=10, sku_code="SKU1"
    )
    detail = get_product_detail(product.slug)
    data = json.loads(detail.json_ld)
    assert data["@type"] == "Product"
    assert data["name"] == product.name_en


@pytest.mark.django_db
def test_json_ld_single_variant_uses_offer() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    detail = get_product_detail(product.slug)
    data = json.loads(detail.json_ld)
    assert data["offers"]["@type"] == "Offer"


@pytest.mark.django_db
def test_json_ld_several_variants_use_aggregate_offer() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    ProductVariantFactory(product=product, is_active=True, selling_price=20, mrp=20)
    detail = get_product_detail(product.slug)
    data = json.loads(detail.json_ld)
    assert data["offers"]["@type"] == "AggregateOffer"
    assert data["offers"]["offerCount"] == 2


@pytest.mark.django_db
def test_json_ld_currency_inr_and_plain_prices() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=95, mrp=100)
    detail = get_product_detail(product.slug)
    data = json.loads(detail.json_ld)
    assert data["offers"]["priceCurrency"] == "INR"
    assert data["offers"]["price"] == "95.00"


@pytest.mark.django_db
def test_json_ld_availability_follows_stock() -> None:
    from apps.inventory.services import receive_stock

    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    v = ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    detail = get_product_detail(product.slug)
    data = json.loads(detail.json_ld)
    assert data["offers"]["availability"] == "https://schema.org/OutOfStock"
    receive_stock(variant=v, quantity=5)
    detail2 = get_product_detail(product.slug)
    data2 = json.loads(detail2.json_ld)
    assert data2["offers"]["availability"] == "https://schema.org/InStock"


@pytest.mark.django_db
def test_json_ld_images_are_absolute_urls() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    detail = get_product_detail(product.slug)
    data = json.loads(detail.json_ld)
    for img in data.get("image", []):
        assert img.startswith("http")


@pytest.mark.django_db
def test_json_ld_escapes_script_end_tag_in_description() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True, description="a </script> b")
    ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    detail = get_product_detail(product.slug)
    assert "</script>" not in detail.json_ld
    data = json.loads(detail.json_ld)
    assert "</script>" in data["description"]


@pytest.mark.django_db
def test_json_ld_gtin_only_when_barcode_set() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10, barcode=None)
    detail = get_product_detail(product.slug)
    data = json.loads(detail.json_ld)
    assert "gtin" not in data
    # single variant with barcode
    brand2 = BrandFactory(is_active=True)
    cat2 = CategoryFactory(is_active=True)
    product2 = ProductFactory(brand=brand2, category=cat2, is_active=True)
    ProductVariantFactory(
        product=product2, is_active=True, selling_price=10, mrp=10, barcode="1234567890123"
    )
    detail2 = get_product_detail(product2.slug)
    data2 = json.loads(detail2.json_ld)
    assert data2.get("gtin") == "1234567890123"
