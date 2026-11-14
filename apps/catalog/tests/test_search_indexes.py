"""The trigram indexes behind the search page (Slice 49, D90).

The planner only uses an index when it is cheaper than a sequential scan, so
each test turns sequential scans off inside its own transaction. That makes the
plan deterministic without depending on how much data the test database holds.
"""

from __future__ import annotations

from importlib import import_module

import pytest
from django.db import connection, transaction
from django.db.migrations import AddIndex

from apps.catalog.models import Product
from apps.catalog.search import get_search_backend
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)

pytestmark = pytest.mark.django_db

# Enough rows that the planner has a real choice to make.
PRODUCT_COUNT = 2000

NEW_INDEX_NAMES = [
    "product_name_en_trgm",
    "product_name_hi_trgm",
    "product_keywords_trgm",
]


@pytest.fixture
def many_products() -> None:
    """A few thousand products, inserted in one statement."""
    brand = BrandFactory(is_active=True)
    category = CategoryFactory(is_active=True)
    Product.objects.bulk_create(
        [
            Product(
                name_en=f"Item {number} xyz",
                name_hi=f"Vastu {number} xyz",
                slug=f"search-item-{number}",
                brand=brand,
                category=category,
                search_keywords=f"xyz keyword {number}",
            )
            for number in range(PRODUCT_COUNT)
        ]
    )


def _plan(field: str) -> str:
    """The query plan for one icontains filter, with sequential scans off."""
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL enable_seqscan = off")
        # **{"name_en__icontains": "xyz"} builds the filter from the field name.
        return Product.objects.filter(**{f"{field}__icontains": "xyz"}).explain()


def _assert_index_used(plan: str, index_name: str) -> None:
    assert index_name in plan, plan
    assert "Index Scan" in plan or "Bitmap Index Scan" in plan, plan


def test_name_en_icontains_can_use_the_trigram_index(many_products: None) -> None:
    _assert_index_used(_plan("name_en"), "product_name_en_trgm")


def test_name_hi_icontains_can_use_the_trigram_index(many_products: None) -> None:
    _assert_index_used(_plan("name_hi"), "product_name_hi_trgm")


def test_keywords_icontains_can_use_the_trigram_index(many_products: None) -> None:
    _assert_index_used(_plan("search_keywords"), "product_keywords_trgm")


def _ranked_product(name: str) -> Product:
    product = ProductFactory(name_en=name, is_active=True, brand=BrandFactory(is_active=True))
    ProductVariantFactory(product=product, is_active=True)
    return product


def test_search_results_are_unchanged_by_the_indexes() -> None:
    exact = _ranked_product("Atta")
    prefix = _ranked_product("Atta Flour")
    full_text = _ranked_product("Whole Atta Pack")

    hits = get_search_backend().search("atta")

    # The score bands still decide the order: an exact name, then a name
    # prefix, then any other full-text match.
    assert [hit.product_id for hit in hits] == [exact.pk, prefix.pk, full_text.pk]


def test_migration_only_adds_indexes() -> None:
    module = import_module("apps.catalog.migrations.0010_product_search_trigram_indexes")
    operations = module.Migration.operations

    assert len(operations) == 3
    assert all(isinstance(operation, AddIndex) for operation in operations)
    assert [operation.index.name for operation in operations] == NEW_INDEX_NAMES
