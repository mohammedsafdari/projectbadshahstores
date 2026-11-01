"""Tests for listing filter application (Slice 24a)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.inventory.services import adjust_stock, receive_stock
from apps.storefront.filters import ListingFilters
from apps.storefront.services import CategoryTree, visible_products

V = ProductVariantFactory
D = Decimal


def _make_product(*, name: str, brand, category, **kwargs) -> Product:
    product = ProductFactory(name_en=name, brand=brand, category=category, is_active=True, **kwargs)
    return product


@pytest.mark.django_db
def test_no_filters_returns_same_products_as_none() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    V(product=product, is_active=True, selling_price=D("10"), mrp=D("10"))
    tree = CategoryTree()
    a = [p.slug for p in visible_products(tree=tree)]
    b = [p.slug for p in visible_products(tree=tree, filters=ListingFilters())]
    assert a == b


@pytest.mark.django_db
def test_brand_filter_single_brand() -> None:
    brand = BrandFactory(is_active=True)
    other = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(brand=brand, category=cat, is_active=True)
    p2 = ProductFactory(brand=other, category=cat, is_active=True)
    V(product=p1, is_active=True, selling_price=D("10"), mrp=D("10"))
    V(product=p2, is_active=True, selling_price=D("10"), mrp=D("10"))
    tree = CategoryTree()
    filters = ListingFilters(brand_slugs=(brand.slug,))
    slugs = sorted(p.slug for p in visible_products(tree=tree, filters=filters))
    assert slugs == [p1.slug]


@pytest.mark.django_db
def test_brand_filter_several_brands_is_or() -> None:
    b1 = BrandFactory(is_active=True)
    b2 = BrandFactory(is_active=True)
    b3 = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    products = []
    for b in (b1, b2, b3):
        p = ProductFactory(brand=b, category=cat, is_active=True)
        V(product=p, is_active=True, selling_price=D("10"), mrp=D("10"))
        products.append(p)
    tree = CategoryTree()
    filters = ListingFilters(brand_slugs=(b1.slug, b2.slug))
    slugs = sorted(p.slug for p in visible_products(tree=tree, filters=filters))
    assert slugs == sorted([products[0].slug, products[1].slug])


@pytest.mark.django_db
def test_brand_filter_unknown_slug_is_ignored() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    V(product=product, is_active=True, selling_price=D("10"), mrp=D("10"))
    tree = CategoryTree()
    filters = ListingFilters(brand_slugs=("nope",))
    slugs = [p.slug for p in visible_products(tree=tree, filters=filters)]
    assert slugs == [product.slug]


@pytest.mark.django_db
def test_brand_filter_unknown_and_known_slug_uses_known() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    V(product=product, is_active=True, selling_price=D("10"), mrp=D("10"))
    tree = CategoryTree()
    filters = ListingFilters(brand_slugs=("nope", brand.slug))
    slugs = [p.slug for p in visible_products(tree=tree, filters=filters)]
    assert slugs == [product.slug]


@pytest.mark.django_db
def test_min_price_filter() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(brand=brand, category=cat, is_active=True)
    p2 = ProductFactory(brand=brand, category=cat, is_active=True)
    V(product=p1, is_active=True, selling_price=D("10"), mrp=D("10"))
    V(product=p2, is_active=True, selling_price=D("20"), mrp=D("20"))
    tree = CategoryTree()
    filters = ListingFilters(min_price=D("15"))
    slugs = [p.slug for p in visible_products(tree=tree, filters=filters)]
    assert slugs == [p2.slug]


@pytest.mark.django_db
def test_max_price_filter() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(brand=brand, category=cat, is_active=True)
    p2 = ProductFactory(brand=brand, category=cat, is_active=True)
    V(product=p1, is_active=True, selling_price=D("10"), mrp=D("10"))
    V(product=p2, is_active=True, selling_price=D("20"), mrp=D("20"))
    tree = CategoryTree()
    filters = ListingFilters(max_price=D("15"))
    slugs = [p.slug for p in visible_products(tree=tree, filters=filters)]
    assert slugs == [p1.slug]


@pytest.mark.django_db
def test_price_range_is_inclusive() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(brand=brand, category=cat, is_active=True)
    V(product=p, is_active=True, selling_price=D("10"), mrp=D("10"))
    tree = CategoryTree()
    filters = ListingFilters(min_price=D("10"), max_price=D("10"))
    slugs = [x.slug for x in visible_products(tree=tree, filters=filters)]
    assert slugs == [p.slug]


@pytest.mark.django_db
def test_price_filter_uses_cheapest_active_variant() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(brand=brand, category=cat, is_active=True)
    V(product=p, is_active=True, selling_price=D("10"), mrp=D("10"))
    V(product=p, is_active=True, selling_price=D("20"), mrp=D("20"))
    tree = CategoryTree()
    filters = ListingFilters(max_price=D("15"))
    slugs = [x.slug for x in visible_products(tree=tree, filters=filters)]
    assert slugs == [p.slug]


@pytest.mark.django_db
def test_inactive_variant_price_ignored_by_price_filter() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(brand=brand, category=cat, is_active=True)
    V(product=p, is_active=False, selling_price=D("10"), mrp=D("10"))
    V(product=p, is_active=True, selling_price=D("20"), mrp=D("20"))
    tree = CategoryTree()
    filters = ListingFilters(max_price=D("15"))
    slugs = [x.slug for x in visible_products(tree=tree, filters=filters)]
    assert slugs == []


@pytest.mark.django_db
def test_own_brand_filter() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(brand=brand, category=cat, is_active=True, is_own_brand=True)
    p2 = ProductFactory(brand=brand, category=cat, is_active=True, is_own_brand=False)
    V(product=p1, is_active=True, selling_price=D("10"), mrp=D("10"))
    V(product=p2, is_active=True, selling_price=D("10"), mrp=D("10"))
    tree = CategoryTree()
    filters = ListingFilters(own_brand=True)
    slugs = [p.slug for p in visible_products(tree=tree, filters=filters)]
    assert slugs == [p1.slug]


@pytest.mark.django_db
def test_veg_only_filter() -> None:
    from apps.catalog.models import Product

    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(brand=brand, category=cat, is_active=True, diet_type=Product.DietType.VEG)
    p2 = ProductFactory(
        brand=brand, category=cat, is_active=True, diet_type=Product.DietType.NON_VEG
    )
    V(product=p1, is_active=True, selling_price=D("10"), mrp=D("10"))
    V(product=p2, is_active=True, selling_price=D("10"), mrp=D("10"))
    tree = CategoryTree()
    filters = ListingFilters(veg_only=True)
    slugs = [p.slug for p in visible_products(tree=tree, filters=filters)]
    assert slugs == [p1.slug]


@pytest.mark.django_db
def test_in_stock_filter_keeps_product_with_stock() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(brand=brand, category=cat, is_active=True)
    p2 = ProductFactory(brand=brand, category=cat, is_active=True)
    v1 = V(product=p1, is_active=True, selling_price=D("10"), mrp=D("10"))
    V(product=p2, is_active=True, selling_price=D("10"), mrp=D("10"))
    receive_stock(variant=v1, quantity=5)
    tree = CategoryTree()
    filters = ListingFilters(in_stock=True)
    slugs = [p.slug for p in visible_products(tree=tree, filters=filters)]
    assert slugs == [p1.slug]


@pytest.mark.django_db
def test_in_stock_filter_drops_zero_and_negative_stock() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(brand=brand, category=cat, is_active=True)
    v = V(product=p, is_active=True, selling_price=D("10"), mrp=D("10"))
    receive_stock(variant=v, quantity=5)
    adjust_stock(variant=v, quantity_change=-5, reason="test")
    tree = CategoryTree()
    filters = ListingFilters(in_stock=True)
    slugs = [x.slug for x in visible_products(tree=tree, filters=filters)]
    assert slugs == []


@pytest.mark.django_db
def test_in_stock_filter_ignores_stock_on_inactive_variant() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(brand=brand, category=cat, is_active=True)
    v = V(product=p, is_active=True, selling_price=D("10"), mrp=D("10"))
    receive_stock(variant=v, quantity=5)
    v.is_active = False
    v.save()
    V(product=p, is_active=True, selling_price=D("10"), mrp=D("10"))
    tree = CategoryTree()
    filters = ListingFilters(in_stock=True)
    slugs = [x.slug for x in visible_products(tree=tree, filters=filters)]
    assert slugs == []


@pytest.mark.django_db
def test_in_stock_filter_one_of_two_variants_is_enough_and_row_not_duplicated() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(brand=brand, category=cat, is_active=True)
    v1 = V(product=p, is_active=True, selling_price=D("10"), mrp=D("10"))
    V(product=p, is_active=True, selling_price=D("20"), mrp=D("20"))
    receive_stock(variant=v1, quantity=5)
    tree = CategoryTree()
    filters = ListingFilters(in_stock=True)
    qs = visible_products(tree=tree, filters=filters)
    assert qs.count() == 1


@pytest.mark.django_db
def test_filters_combine_with_and() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(brand=brand, category=cat, is_active=True, is_own_brand=True)
    V(product=p, is_active=True, selling_price=D("10"), mrp=D("10"))
    receive_stock(variant=p.variants.first(), quantity=5)
    tree = CategoryTree()
    filters = ListingFilters(own_brand=True, in_stock=True)
    slugs = [x.slug for x in visible_products(tree=tree, filters=filters)]
    assert slugs == [p.slug]


@pytest.mark.django_db
def test_filters_keep_sort_order() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(name_en="B", brand=brand, category=cat, is_active=True)
    p2 = ProductFactory(name_en="A", brand=brand, category=cat, is_active=True)
    V(product=p1, is_active=True, selling_price=D("10"), mrp=D("10"))
    V(product=p2, is_active=True, selling_price=D("20"), mrp=D("20"))
    tree = CategoryTree()
    filters = ListingFilters(max_price=D("15"))
    cards_names = [p.name_en for p in visible_products(tree=tree, filters=filters)]
    assert cards_names == ["B"]
