"""Tests for storefront listing services (Slice 23c/23f)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.inventory.services import adjust_stock, receive_stock
from apps.storefront.services import (
    CategoryTree,
    build_cards,
    visible_products,
)


@pytest.mark.django_db
def test_inactive_product_hidden() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=False)
    ProductVariantFactory(product=product, is_active=True)
    tree = CategoryTree()
    assert list(visible_products(tree=tree)) == []


@pytest.mark.django_db
def test_inactive_brand_hidden() -> None:
    brand = BrandFactory(is_active=False)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True)
    tree = CategoryTree()
    assert list(visible_products(tree=tree)) == []


@pytest.mark.django_db
def test_inactive_category_hidden() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=False)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True)
    tree = CategoryTree()
    assert list(visible_products(tree=tree)) == []


@pytest.mark.django_db
def test_inactive_ancestor_hidden() -> None:
    brand = BrandFactory(is_active=True)
    parent = CategoryFactory(is_active=False)
    child = CategoryFactory(is_active=True, parent=parent)
    product = ProductFactory(brand=brand, category=child, is_active=True)
    ProductVariantFactory(product=product, is_active=True)
    tree = CategoryTree()
    assert list(visible_products(tree=tree)) == []


@pytest.mark.django_db
def test_no_active_variant_hidden() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=False)
    tree = CategoryTree()
    assert list(visible_products(tree=tree)) == []


@pytest.mark.django_db
def test_lead_variant_choice_and_tie() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, selling_price=Decimal("50.00"), mrp=Decimal("60.00"))
    ProductVariantFactory(product=product, selling_price=Decimal("40.00"), mrp=Decimal("60.00"))
    tree = CategoryTree()
    qs = visible_products(tree=tree)
    cards = build_cards(qs)
    assert cards[0].lead_price == Decimal("40.00")


@pytest.mark.django_db
def test_in_stock_logic() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    variant = ProductVariantFactory(product=product, is_active=True)
    tree = CategoryTree()
    # no movements -> out of stock
    cards = build_cards(visible_products(tree=tree))
    assert cards[0].in_stock is False
    receive_stock(variant=variant, quantity=5)
    cards = build_cards(visible_products(tree=tree))
    assert cards[0].in_stock is True


@pytest.mark.django_db
def test_sort_options() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(name_en="B", brand=brand, category=cat, is_active=True)
    p2 = ProductFactory(name_en="A", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p1, selling_price=Decimal("10.00"), mrp=Decimal("10.00"))
    ProductVariantFactory(product=p2, selling_price=Decimal("20.00"), mrp=Decimal("20.00"))
    tree = CategoryTree()
    names = [c.name for c in build_cards(visible_products(tree=tree, sort="name"))]
    assert names == ["A", "B"]


@pytest.mark.django_db
def test_category_includes_descendants() -> None:
    brand = BrandFactory(is_active=True)
    parent = CategoryFactory(is_active=True)
    child = CategoryFactory(is_active=True, parent=parent)
    product = ProductFactory(brand=brand, category=child, is_active=True)
    ProductVariantFactory(product=product, is_active=True)
    tree = CategoryTree()
    qs = visible_products(tree=tree, category=parent)
    assert qs.count() == 1


@pytest.mark.django_db
def test_build_cards_query_count() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    for _ in range(3):
        p = ProductFactory(brand=brand, category=cat, is_active=True)
        ProductVariantFactory(product=p, is_active=True)
    tree = CategoryTree()
    qs = visible_products(tree=tree)
    with CaptureQueriesContext(connection) as ctx3:
        build_cards(list(qs)[:3])
    # 24 products
    for _ in range(21):
        p = ProductFactory(brand=brand, category=cat, is_active=True)
        ProductVariantFactory(product=p, is_active=True)
    qs2 = visible_products(tree=tree)
    with CaptureQueriesContext(connection) as ctx24:
        build_cards(list(qs2)[:24])
    assert len(ctx3.captured_queries) == len(ctx24.captured_queries)


@pytest.mark.django_db
def test_only_archived_variants_hidden() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=False)
    tree = CategoryTree()
    assert list(visible_products(tree=tree)) == []


@pytest.mark.django_db
def test_lead_variant_tie_uses_lowest_id() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    v_first = ProductVariantFactory(
        product=product, selling_price=Decimal("40.00"), mrp=Decimal("60.00")
    )
    ProductVariantFactory(product=product, selling_price=Decimal("40.00"), mrp=Decimal("50.00"))
    tree = CategoryTree()
    cards = build_cards(visible_products(tree=tree))
    assert cards[0].mrp == v_first.mrp


@pytest.mark.django_db
def test_has_more_false_for_one_active_variant_and_archived_ignored() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True)
    ProductVariantFactory(product=product, is_active=False)
    tree = CategoryTree()
    cards = build_cards(visible_products(tree=tree))
    assert cards[0].has_more is False


@pytest.mark.django_db
def test_has_more_true_for_two_active_variants() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True)
    ProductVariantFactory(product=product, is_active=True)
    tree = CategoryTree()
    cards = build_cards(visible_products(tree=tree))
    assert cards[0].has_more is True


@pytest.mark.django_db
def test_zero_on_hand_is_out_of_stock() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    variant = ProductVariantFactory(product=product, is_active=True)
    receive_stock(variant=variant, quantity=5)
    adjust_stock(variant=variant, quantity_change=-5, reason="test")
    tree = CategoryTree()
    cards = build_cards(visible_products(tree=tree))
    assert cards[0].in_stock is False


@pytest.mark.django_db
def test_negative_on_hand_is_out_of_stock() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    variant = ProductVariantFactory(product=product, is_active=True)
    receive_stock(variant=variant, quantity=5)
    adjust_stock(variant=variant, quantity_change=-10, reason="test")
    tree = CategoryTree()
    cards = build_cards(visible_products(tree=tree))
    assert cards[0].in_stock is False


@pytest.mark.django_db
def test_stock_on_archived_variant_does_not_count() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    variant = ProductVariantFactory(product=product, is_active=True)
    receive_stock(variant=variant, quantity=5)
    variant.is_active = False
    variant.save()
    # add an active variant so product stays visible
    ProductVariantFactory(product=product, is_active=True)
    tree = CategoryTree()
    cards = build_cards(visible_products(tree=tree))
    assert cards[0].in_stock is False


@pytest.mark.django_db
def test_stock_on_second_variant_counts() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True)
    v2 = ProductVariantFactory(product=product, is_active=True)
    receive_stock(variant=v2, quantity=5)
    tree = CategoryTree()
    cards = build_cards(visible_products(tree=tree))
    assert cards[0].in_stock is True


@pytest.mark.django_db
def test_sort_price_asc() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(name_en="B", brand=brand, category=cat, is_active=True)
    p2 = ProductFactory(name_en="A", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p1, selling_price=Decimal("20.00"), mrp=Decimal("20.00"))
    ProductVariantFactory(product=p2, selling_price=Decimal("10.00"), mrp=Decimal("10.00"))
    tree = CategoryTree()
    cards = build_cards(visible_products(tree=tree, sort="price_asc"))
    assert [c.name for c in cards] == ["A", "B"]


@pytest.mark.django_db
def test_sort_price_desc() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(name_en="B", brand=brand, category=cat, is_active=True)
    p2 = ProductFactory(name_en="A", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p1, selling_price=Decimal("20.00"), mrp=Decimal("20.00"))
    ProductVariantFactory(product=p2, selling_price=Decimal("10.00"), mrp=Decimal("10.00"))
    tree = CategoryTree()
    cards = build_cards(visible_products(tree=tree, sort="price_desc"))
    assert [c.name for c in cards] == ["B", "A"]


@pytest.mark.django_db
def test_sort_newest() -> None:
    from datetime import timedelta

    from django.utils import timezone

    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(name_en="Old", brand=brand, category=cat, is_active=True)
    p2 = ProductFactory(name_en="New", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p1, is_active=True)
    ProductVariantFactory(product=p2, is_active=True)
    now = timezone.now()
    Product.objects.filter(pk=p1.pk).update(created_at=now - timedelta(days=1))
    Product.objects.filter(pk=p2.pk).update(created_at=now)
    tree = CategoryTree()
    cards = build_cards(visible_products(tree=tree, sort="newest"))
    assert [c.name for c in cards] == ["New", "Old"]


@pytest.mark.django_db
def test_price_ties_ordered_by_pk() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(name_en="First", brand=brand, category=cat, is_active=True)
    p2 = ProductFactory(name_en="Second", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p1, selling_price=Decimal("10.00"), mrp=Decimal("10.00"))
    ProductVariantFactory(product=p2, selling_price=Decimal("10.00"), mrp=Decimal("10.00"))
    tree = CategoryTree()
    asc = [c.name for c in build_cards(visible_products(tree=tree, sort="price_asc"))]
    assert asc == ["First", "Second"]


@pytest.mark.django_db
def test_unknown_sort_falls_back_to_name() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(name_en="B", brand=brand, category=cat, is_active=True)
    p2 = ProductFactory(name_en="A", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p1, is_active=True)
    ProductVariantFactory(product=p2, is_active=True)
    tree = CategoryTree()
    cards = build_cards(visible_products(tree=tree, sort="nope"))
    assert [c.name for c in cards] == ["A", "B"]


@pytest.mark.django_db
def test_name_sort_is_case_insensitive() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(name_en="b", brand=brand, category=cat, is_active=True)
    p2 = ProductFactory(name_en="A", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p1, is_active=True)
    ProductVariantFactory(product=p2, is_active=True)
    tree = CategoryTree()
    cards = build_cards(visible_products(tree=tree, sort="name"))
    assert [c.name for c in cards] == ["A", "b"]


@pytest.mark.django_db
def test_visible_products_page_slice_uses_one_query() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    for _ in range(3):
        p = ProductFactory(brand=brand, category=cat, is_active=True)
        ProductVariantFactory(product=p, is_active=True)
    tree = CategoryTree()
    with CaptureQueriesContext(connection) as ctx:
        list(visible_products(tree=tree)[:24])
    assert len(ctx.captured_queries) == 1
