"""Tests for product detail JSON-LD (Slice 25)."""

from __future__ import annotations

import json
import re

import pytest
from django.test import Client

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
def test_json_ld_images_are_absolute_urls(tmp_path, settings) -> None:
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    from apps.catalog.models import ProductImage

    settings.MEDIA_ROOT = tmp_path

    def make() -> SimpleUploadedFile:
        buf = io.BytesIO()
        Image.new("RGB", (20, 20), color=(100, 150, 200)).save(buf, format="JPEG")
        buf.seek(0)
        return SimpleUploadedFile("p.jpg", buf.read(), content_type="image/jpeg")

    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    img0 = ProductImage.objects.create(product=product, image=make(), sort_order=0)
    img1 = ProductImage.objects.create(product=product, image=make(), sort_order=1)
    img2 = ProductImage.objects.create(product=product, image=make(), sort_order=2)
    img0.refresh_from_db()
    img1.refresh_from_db()
    img2.refresh_from_db()
    detail = get_product_detail(product.slug, build_url=lambda path: f"http://testserver{path}")
    data = json.loads(detail.json_ld)
    assert data["image"] == [
        f"http://testserver{img0.medium.url}",
        f"http://testserver{img1.thumbnail.url}",
        f"http://testserver{img2.thumbnail.url}",
    ]
    assert "/medium/" in data["image"][0]
    assert "/thumbs/" in data["image"][1]
    assert "/thumbs/" in data["image"][2]
    assert data["image"][0] != f"http://testserver{img0.image.url}"


@pytest.mark.django_db
def test_aggregate_offer_in_stock_when_any_variant_in_stock() -> None:
    from apps.inventory.services import receive_stock

    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    v1 = ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    ProductVariantFactory(product=product, is_active=True, selling_price=20, mrp=20)
    receive_stock(variant=v1, quantity=5)
    detail = get_product_detail(product.slug)
    data = json.loads(detail.json_ld)
    assert data["offers"]["availability"] == "https://schema.org/InStock"


@pytest.mark.django_db
def test_aggregate_offer_out_of_stock_when_no_variant_in_stock() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    ProductVariantFactory(product=product, is_active=True, selling_price=20, mrp=20)
    detail = get_product_detail(product.slug)
    data = json.loads(detail.json_ld)
    assert data["offers"]["availability"] == "https://schema.org/OutOfStock"


@pytest.mark.django_db
def test_json_ld_main_image_is_medium(tmp_path, settings) -> None:
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    from apps.catalog.models import ProductImage

    settings.MEDIA_ROOT = tmp_path

    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), color=(100, 150, 200)).save(buf, format="JPEG")
    buf.seek(0)
    img = ProductImage.objects.create(
        product=product, image=SimpleUploadedFile("m.jpg", buf.read(), content_type="image/jpeg")
    )
    img.refresh_from_db()
    detail = get_product_detail(product.slug, build_url=lambda path: f"http://testserver{path}")
    data = json.loads(detail.json_ld)
    assert data["image"] == [f"http://testserver{img.medium.url}"]
    assert "/medium/" in data["image"][0]
    assert data["image"][0] != f"http://testserver{img.image.url}"


@pytest.mark.django_db
def test_json_ld_sku_is_cheapest_variant_sku() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(
        product=product, is_active=True, selling_price=20, mrp=20, sku_code="EXPENSIVE"
    )
    ProductVariantFactory(
        product=product, is_active=True, selling_price=10, mrp=10, sku_code="CHEAP"
    )
    detail = get_product_detail(product.slug)
    data = json.loads(detail.json_ld)
    assert data["sku"] == "CHEAP"


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


@pytest.mark.django_db
def test_detail_view_json_ld_image_urls_are_absolute(client: Client, tmp_path, settings) -> None:
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    from apps.catalog.models import ProductImage

    settings.MEDIA_ROOT = tmp_path

    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), color=(100, 150, 200)).save(buf, format="JPEG")
    buf.seek(0)
    image = ProductImage.objects.create(
        product=product, image=SimpleUploadedFile("v.jpg", buf.read(), content_type="image/jpeg")
    )
    image.refresh_from_db()
    response = client.get(f"/product/{product.slug}/")
    html = response.content.decode()
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    assert match is not None
    data = json.loads(match.group(1))
    assert data["image"]
    for url in data["image"]:
        assert url.startswith("http://testserver")
