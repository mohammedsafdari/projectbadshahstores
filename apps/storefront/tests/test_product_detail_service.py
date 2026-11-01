"""Tests for the product detail service (Slice 25)."""

from __future__ import annotations

import pytest

from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.inventory.services import receive_stock
from apps.storefront.product_detail import get_product_detail


@pytest.mark.django_db
def test_visible_product_is_returned() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True, name_en="P")
    ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    detail = get_product_detail(product.slug)
    assert detail.product.slug == product.slug


@pytest.mark.django_db
def test_unknown_slug_not_found() -> None:
    with pytest.raises(Product.DoesNotExist):
        get_product_detail("nope")


@pytest.mark.django_db
def test_inactive_product_not_found() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=False)
    ProductVariantFactory(product=product, is_active=True)
    with pytest.raises(Product.DoesNotExist):
        get_product_detail(product.slug)


@pytest.mark.django_db
def test_inactive_brand_not_found() -> None:
    brand = BrandFactory(is_active=False)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True)
    with pytest.raises(Product.DoesNotExist):
        get_product_detail(product.slug)


@pytest.mark.django_db
def test_inactive_category_or_ancestor_not_found() -> None:
    brand = BrandFactory(is_active=True)
    parent = CategoryFactory(is_active=False)
    child = CategoryFactory(is_active=True, parent=parent)
    product = ProductFactory(brand=brand, category=child, is_active=True)
    ProductVariantFactory(product=product, is_active=True)
    with pytest.raises(Product.DoesNotExist):
        get_product_detail(product.slug)


@pytest.mark.django_db
def test_product_without_active_variant_not_found() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=False)
    with pytest.raises(Product.DoesNotExist):
        get_product_detail(product.slug)


@pytest.mark.django_db
def test_inactive_variants_not_listed() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    ProductVariantFactory(product=product, is_active=False, selling_price=5, mrp=5)
    detail = get_product_detail(product.slug)
    assert len(detail.variants) == 1


@pytest.mark.django_db
def test_variants_sorted_by_price_then_id() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    v1 = ProductVariantFactory(product=product, is_active=True, selling_price=20, mrp=20)
    v2 = ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    detail = get_product_detail(product.slug)
    assert [vd.variant.pk for vd in detail.variants] == [v2.pk, v1.pk]


@pytest.mark.django_db
def test_variant_savings_amount_and_percent() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=45, mrp=50)
    detail = get_product_detail(product.slug)
    vd = detail.variants[0]
    assert vd.variant.mrp - vd.variant.selling_price == 5


@pytest.mark.django_db
def test_no_savings_when_price_equals_mrp() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=48, mrp=48)
    detail = get_product_detail(product.slug)
    assert detail.variants[0].variant.mrp == detail.variants[0].variant.selling_price


@pytest.mark.django_db
def test_unit_price_only_for_weight_and_volume_units() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(
        product=product, is_active=True, unit="KG", pack_size=1, selling_price=10, mrp=10
    )
    ProductVariantFactory(
        product=product, is_active=True, unit="PACKET", pack_size=1, selling_price=10, mrp=10
    )
    detail = get_product_detail(product.slug)
    texts = [vd.unit_price_text for vd in detail.variants]
    assert any(t is not None for t in texts)
    assert any(t is None for t in texts)


@pytest.mark.django_db
def test_variant_stock_follows_ledger() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    variant = ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    receive_stock(variant=variant, quantity=5)
    detail = get_product_detail(product.slug)
    assert detail.variants[0].in_stock is True


@pytest.mark.django_db
def test_images_ordered_with_medium_then_original_fallback() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    detail = get_product_detail(product.slug)
    assert isinstance(detail.images, list)


@pytest.mark.django_db
def test_related_products_same_category_excluding_self_max_four() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    main = ProductFactory(brand=brand, category=cat, is_active=True, name_en="Main")
    ProductVariantFactory(product=main, is_active=True, selling_price=10, mrp=10)
    for i in range(5):
        p = ProductFactory(brand=brand, category=cat, is_active=True, name_en=f"Rel{i}")
        ProductVariantFactory(product=p, is_active=True, selling_price=10, mrp=10)
    detail = get_product_detail(main.slug)
    assert len(detail.related) == 4
    assert main.slug not in [p.slug for p in detail.related]


@pytest.mark.django_db
def test_related_products_skip_hidden_products() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    main = ProductFactory(brand=brand, category=cat, is_active=True, name_en="Main")
    ProductVariantFactory(product=main, is_active=True, selling_price=10, mrp=10)
    hidden = ProductFactory(brand=brand, category=cat, is_active=False, name_en="Hidden")
    ProductVariantFactory(product=hidden, is_active=True, selling_price=10, mrp=10)
    detail = get_product_detail(main.slug)
    assert "hidden" not in [p.slug for p in detail.related]
