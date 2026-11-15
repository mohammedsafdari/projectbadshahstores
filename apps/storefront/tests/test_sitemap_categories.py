"""Which category pages the sitemap lists (Slice 52, D93).

A category page is public only when it and all its ancestors are active AND it
holds a product the listing pages show, in the category itself or in one of its
children. A category page always shows the products of its own sub-categories,
so an empty parent of a stocked child is a real page and an empty leaf is not.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.storefront.seo import sitemap_paths

pytestmark = pytest.mark.django_db


def _category_path(category) -> str:
    return reverse("category_detail", args=[category.slug])


def _product_in(category, *, is_active: bool = True) -> None:
    """One product the listing pages show, in this category."""
    brand = BrandFactory(is_active=True)
    product = ProductFactory(brand=brand, category=category, is_active=is_active)
    ProductVariantFactory(product=product, is_active=True)


def test_category_with_a_visible_product_is_listed() -> None:
    category = CategoryFactory(is_active=True)
    _product_in(category)

    assert _category_path(category) in sitemap_paths()


def test_category_with_no_products_is_skipped() -> None:
    category = CategoryFactory(is_active=True)

    assert _category_path(category) not in sitemap_paths()


def test_category_with_only_inactive_products_is_skipped() -> None:
    category = CategoryFactory(is_active=True)
    _product_in(category, is_active=False)

    assert _category_path(category) not in sitemap_paths()


def test_parent_is_listed_when_only_a_child_has_a_visible_product() -> None:
    parent = CategoryFactory(is_active=True)
    child = CategoryFactory(is_active=True, parent=parent)
    _product_in(child)

    paths = sitemap_paths()

    # The category page of the parent lists its children's products too.
    assert _category_path(parent) in paths
    assert _category_path(child) in paths


def test_empty_child_is_skipped_when_the_parent_has_products() -> None:
    parent = CategoryFactory(is_active=True)
    child = CategoryFactory(is_active=True, parent=parent)
    _product_in(parent)

    paths = sitemap_paths()

    assert _category_path(parent) in paths
    assert _category_path(child) not in paths


def test_inactive_ancestor_still_hides_a_category_with_products() -> None:
    grandparent = CategoryFactory(is_active=False)
    parent = CategoryFactory(is_active=True, parent=grandparent)
    child = CategoryFactory(is_active=True, parent=parent)
    _product_in(child)

    paths = sitemap_paths()

    assert _category_path(grandparent) not in paths
    assert _category_path(parent) not in paths
    assert _category_path(child) not in paths


def _category_with_product(number: int):
    category = CategoryFactory(is_active=True, name=f"Sitemap Category {number}")
    _product_in(category)
    return category


def test_sitemap_query_count_does_not_grow_with_categories() -> None:
    for number in range(3):
        _category_with_product(number)

    with CaptureQueriesContext(connection) as small:
        sitemap_paths()

    for number in range(3, 20):
        _category_with_product(number)
    with CaptureQueriesContext(connection) as large:
        paths = sitemap_paths()

    assert len(large.captured_queries) == len(small.captured_queries)
    assert sum(1 for path in paths if path.startswith("/category/")) == 20
