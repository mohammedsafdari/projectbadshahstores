"""Which brand pages the sitemap lists (Slice 49, D90).

A brand page is public only when the brand is active AND holds at least one
product the listing pages show, so a brand with nothing in it never reaches a
search engine.
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


def _brand_path(brand) -> str:
    return reverse("brand_detail", args=[brand.slug])


def test_brand_with_visible_product_is_listed() -> None:
    brand = BrandFactory(is_active=True)
    category = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=category, is_active=True)
    ProductVariantFactory(product=product, is_active=True)

    assert _brand_path(brand) in sitemap_paths()


def test_brand_with_no_products_is_skipped() -> None:
    brand = BrandFactory(is_active=True)

    assert _brand_path(brand) not in sitemap_paths()


def test_brand_with_only_inactive_products_is_skipped() -> None:
    brand = BrandFactory(is_active=True)
    category = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=category, is_active=False)
    ProductVariantFactory(product=product, is_active=True)

    assert _brand_path(brand) not in sitemap_paths()


def test_brand_with_product_in_inactive_category_is_skipped() -> None:
    brand = BrandFactory(is_active=True)
    category = CategoryFactory(is_active=False)
    product = ProductFactory(brand=brand, category=category, is_active=True)
    ProductVariantFactory(product=product, is_active=True)

    assert _brand_path(brand) not in sitemap_paths()


def test_inactive_brand_is_skipped() -> None:
    brand = BrandFactory(is_active=False)
    category = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=category, is_active=True)
    ProductVariantFactory(product=product, is_active=True)

    assert _brand_path(brand) not in sitemap_paths()


def _brand_with_product(number: int):
    brand = BrandFactory(is_active=True, name=f"Sitemap Brand {number}")
    category = CategoryFactory(is_active=True)
    product = ProductFactory(
        brand=brand,
        category=category,
        name_en=f"Sitemap Item {number}",
        is_active=True,
    )
    ProductVariantFactory(product=product, is_active=True)
    return brand


def test_sitemap_query_count_does_not_grow_with_brands() -> None:
    for number in range(3):
        _brand_with_product(number)

    with CaptureQueriesContext(connection) as small:
        sitemap_paths()

    for number in range(3, 20):
        _brand_with_product(number)
    with CaptureQueriesContext(connection) as large:
        paths = sitemap_paths()

    assert len(large.captured_queries) == len(small.captured_queries)
    assert sum(1 for path in paths if path.startswith("/brand/")) == 20
