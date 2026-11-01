"""Tests for filter options (Slice 24a)."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.storefront.filter_options import brand_options
from apps.storefront.services import CategoryTree


@pytest.mark.django_db
def test_brand_options_lists_brands_with_visible_products() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True)
    tree = CategoryTree()
    options = brand_options(tree=tree)
    assert [b.slug for b in options] == [brand.slug]


@pytest.mark.django_db
def test_brand_options_skip_inactive_brand_and_products_without_active_variant() -> None:
    inactive = BrandFactory(is_active=False)
    active = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(brand=inactive, category=cat, is_active=True)
    ProductVariantFactory(product=p1, is_active=True)
    p2 = ProductFactory(brand=active, category=cat, is_active=True)
    ProductVariantFactory(product=p2, is_active=False)
    tree = CategoryTree()
    options = brand_options(tree=tree)
    assert [b.slug for b in options] == []


@pytest.mark.django_db
def test_brand_options_scoped_to_category_and_descendants() -> None:
    brand = BrandFactory(is_active=True)
    parent = CategoryFactory(is_active=True)
    child = CategoryFactory(is_active=True, parent=parent)
    other = CategoryFactory(is_active=True)
    p1 = ProductFactory(brand=brand, category=child, is_active=True)
    ProductVariantFactory(product=p1, is_active=True)
    p2 = ProductFactory(brand=brand, category=other, is_active=True)
    ProductVariantFactory(product=p2, is_active=True)
    tree = CategoryTree()
    options = brand_options(tree=tree, category=parent)
    assert [b.slug for b in options] == [brand.slug]


@pytest.mark.django_db
def test_brand_options_sorted_by_name_ignoring_case() -> None:
    b1 = BrandFactory(name="zebra", slug="zebra", is_active=True)
    b2 = BrandFactory(name="Apple", slug="apple", is_active=True)
    cat = CategoryFactory(is_active=True)
    for b in (b1, b2):
        p = ProductFactory(brand=b, category=cat, is_active=True)
        ProductVariantFactory(product=p, is_active=True)
    tree = CategoryTree()
    options = brand_options(tree=tree)
    assert [b.name for b in options] == ["Apple", "zebra"]


@pytest.mark.django_db
def test_brand_options_each_brand_once() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    for _ in range(3):
        p = ProductFactory(brand=brand, category=cat, is_active=True)
        ProductVariantFactory(product=p, is_active=True)
    tree = CategoryTree()
    options = brand_options(tree=tree)
    assert len(options) == 1


@pytest.mark.django_db
def test_brand_options_query_count_is_constant() -> None:
    cat = CategoryFactory(is_active=True)
    brands = [BrandFactory(is_active=True) for _ in range(5)]
    for b in brands[:1]:
        p = ProductFactory(brand=b, category=cat, is_active=True)
        ProductVariantFactory(product=p, is_active=True)
    tree = CategoryTree()
    with CaptureQueriesContext(connection) as ctx1:
        brand_options(tree=tree)
    for b in brands[1:]:
        p = ProductFactory(brand=b, category=cat, is_active=True)
        ProductVariantFactory(product=p, is_active=True)
    tree2 = CategoryTree()
    with CaptureQueriesContext(connection) as ctx5:
        brand_options(tree=tree2)
    assert len(ctx1.captured_queries) == len(ctx5.captured_queries)
