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
    in_scope = BrandFactory(is_active=True)
    out_of_scope = BrandFactory(is_active=True)
    parent = CategoryFactory(is_active=True)
    child = CategoryFactory(is_active=True, parent=parent)
    other = CategoryFactory(is_active=True)
    # in_scope sells in the child category, out_of_scope only in an unrelated one.
    p1 = ProductFactory(brand=in_scope, category=child, is_active=True)
    ProductVariantFactory(product=p1, is_active=True)
    p2 = ProductFactory(brand=out_of_scope, category=other, is_active=True)
    ProductVariantFactory(product=p2, is_active=True)
    tree = CategoryTree()
    assert [b.slug for b in brand_options(tree=tree, category=parent)] == [in_scope.slug]
    # Without a category, both brands are offered.
    assert sorted(b.slug for b in brand_options(tree=tree)) == sorted(
        [in_scope.slug, out_of_scope.slug]
    )


@pytest.mark.django_db
def test_brand_options_sorted_by_name_ignoring_case() -> None:
    # Created in the order banana, Cherry, apple, so neither id order nor plain
    # (case-sensitive) sorting gives the expected result by accident.
    brands = [
        BrandFactory(name="banana", slug="banana", is_active=True),
        BrandFactory(name="Cherry", slug="cherry", is_active=True),
        BrandFactory(name="apple", slug="apple", is_active=True),
    ]
    cat = CategoryFactory(is_active=True)
    for b in brands:
        p = ProductFactory(brand=b, category=cat, is_active=True)
        ProductVariantFactory(product=p, is_active=True)
    tree = CategoryTree()
    options = brand_options(tree=tree)
    assert [b.name for b in options] == ["apple", "banana", "Cherry"]


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
