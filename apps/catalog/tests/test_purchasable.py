"""Tests for the purchasable rule: the storefront visibility rule (D50, D60)."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.catalog.models import Category
from apps.catalog.purchasable import purchasable_variant_ids
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)

pytestmark = pytest.mark.django_db


def test_an_active_variant_is_purchasable() -> None:
    variant = ProductVariantFactory()

    assert purchasable_variant_ids([variant.pk]) == {variant.pk}


def test_an_archived_variant_is_not_purchasable() -> None:
    variant = ProductVariantFactory(is_active=False)

    assert purchasable_variant_ids([variant.pk]) == set()


def test_an_archived_product_hides_the_variant() -> None:
    variant = ProductVariantFactory(product=ProductFactory(is_active=False))

    assert purchasable_variant_ids([variant.pk]) == set()


def test_an_archived_brand_hides_the_variant() -> None:
    brand = BrandFactory(is_active=False)
    product = ProductFactory(brand=brand)
    variant = ProductVariantFactory(product=product)

    assert purchasable_variant_ids([variant.pk]) == set()


def test_an_archived_category_hides_the_variant() -> None:
    variant = ProductVariantFactory(
        product=ProductFactory(category=CategoryFactory(is_active=False))
    )

    assert purchasable_variant_ids([variant.pk]) == set()


def test_an_archived_parent_category_hides_the_variant() -> None:
    parent = CategoryFactory()
    child = CategoryFactory(parent=parent)
    parent.is_active = False
    parent.save(update_fields=["is_active"])
    variant = ProductVariantFactory(product=ProductFactory(category=child))

    assert purchasable_variant_ids([variant.pk]) == set()


def test_a_category_loop_does_not_hang_and_hides_the_variant() -> None:
    first = CategoryFactory()
    second = CategoryFactory(parent=first)
    # Direct save() skips clean(), so this is how a cycle can reach the table.
    first.parent = second
    first.save(update_fields=["parent"])
    variant = ProductVariantFactory(product=ProductFactory(category=first))

    assert purchasable_variant_ids([variant.pk]) == set()


def test_only_purchasable_ids_come_back_from_a_mixed_list() -> None:
    good = ProductVariantFactory()
    archived = ProductVariantFactory(is_active=False)
    hidden_product = ProductVariantFactory(product=ProductFactory(is_active=False))

    result = purchasable_variant_ids([good.pk, archived.pk, hidden_product.pk])

    assert result == {good.pk}


def test_query_count_does_not_grow_with_the_number_of_variants() -> None:
    variants = [ProductVariantFactory() for _ in range(10)]

    with CaptureQueriesContext(connection) as one:
        purchasable_variant_ids([variants[0].pk])
    with CaptureQueriesContext(connection) as ten:
        purchasable_variant_ids([variant.pk for variant in variants])

    assert len(one.captured_queries) == len(ten.captured_queries)


def test_empty_input_returns_an_empty_set() -> None:
    # A category exists, so a wrong implementation that reads rows for an empty
    # list would still return something here.
    Category.objects.create(name="Unused", slug="unused")

    assert purchasable_variant_ids([]) == set()
