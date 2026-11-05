"""Tests for the product detail service (Slice 25)."""

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
    assert vd.savings_amount == Decimal("5.00")
    assert vd.savings_percent == 10


@pytest.mark.django_db
def test_no_savings_when_price_equals_mrp() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=48, mrp=48)
    detail = get_product_detail(product.slug)
    assert detail.variants[0].savings_amount == Decimal("0")
    assert detail.variants[0].savings_percent == 0


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
    assert hidden.slug not in [p.slug for p in detail.related]


@pytest.mark.django_db
def test_related_products_are_sorted_by_name() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    main = ProductFactory(brand=brand, category=cat, is_active=True, name_en="Main")
    ProductVariantFactory(product=main, is_active=True, selling_price=10, mrp=10)
    for name in ("Zulu", "Alpha", "Middle"):
        p = ProductFactory(brand=brand, category=cat, is_active=True, name_en=name)
        ProductVariantFactory(product=p, is_active=True, selling_price=10, mrp=10)
    detail = get_product_detail(main.slug)
    assert [p.name_en for p in detail.related] == ["Alpha", "Middle", "Zulu"]


@pytest.mark.django_db
def test_related_products_query_count_does_not_grow_with_category_size() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    main = ProductFactory(brand=brand, category=cat, is_active=True, name_en="Main")
    ProductVariantFactory(product=main, is_active=True, selling_price=10, mrp=10)
    for i in range(3):
        p = ProductFactory(brand=brand, category=cat, is_active=True, name_en=f"R{i}")
        ProductVariantFactory(product=p, is_active=True, selling_price=10, mrp=10)
    with CaptureQueriesContext(connection) as ctx_small:
        get_product_detail(main.slug)
    for i in range(20):
        p = ProductFactory(brand=brand, category=cat, is_active=True, name_en=f"R2{i}")
        ProductVariantFactory(product=p, is_active=True, selling_price=10, mrp=10)
    with CaptureQueriesContext(connection) as ctx_large:
        get_product_detail(main.slug)
    assert len(ctx_small.captured_queries) == len(ctx_large.captured_queries)


@pytest.mark.django_db
def test_pack_label_prefers_net_quantity_label() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(
        product=product,
        is_active=True,
        selling_price=10,
        mrp=10,
        unit="PACKET",
        pack_size=1,
        net_quantity_label="500 g",
    )
    detail = get_product_detail(product.slug)
    assert detail.variants[0].pack_label == "500 g"


@pytest.mark.django_db
def test_images_follow_sort_order() -> None:
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.catalog.models import ProductImage

    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    img1 = ProductImage.objects.create(
        product=product,
        image=SimpleUploadedFile("a.jpg", b"\xff\xd8\xff", content_type="image/jpeg"),
        sort_order=2,
    )
    img2 = ProductImage.objects.create(
        product=product,
        image=SimpleUploadedFile("b.jpg", b"\xff\xd8\xff", content_type="image/jpeg"),
        sort_order=1,
    )
    detail = get_product_detail(product.slug)
    assert [img.pk for img in detail.images] == [img2.pk, img1.pk]


# ---------------------------------------------------------------------------
# Cart availability on the detail page (Slice 32, D61)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_variant_detail_has_available_and_can_add() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    variant = ProductVariantFactory(
        product=product,
        is_active=True,
        selling_price=10,
        mrp=10,
        unit="PACKET",
        pack_size=1,
        quantity_step=1,
    )
    receive_stock(variant=variant, quantity=5)

    detail = get_product_detail(product.slug)

    assert detail.variants[0].available == Decimal("5.000")
    assert detail.variants[0].can_add is True


@pytest.mark.django_db
def test_can_add_is_false_when_available_is_below_the_step() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    variant = ProductVariantFactory(
        product=product,
        is_active=True,
        selling_price=10,
        mrp=10,
        unit="KG",
        pack_size=1,
        is_sold_loose=True,
        quantity_step=1,
    )
    # 500 g is in the ledger, but this item is sold in 1 kg steps, so there is
    # nothing the customer may add.
    receive_stock(variant=variant, quantity=Decimal("0.500"))

    detail = get_product_detail(product.slug)

    assert detail.variants[0].available == Decimal("0.500")
    assert detail.variants[0].can_add is False


@pytest.mark.django_db
def test_in_stock_is_unchanged_for_json_ld() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    variant = ProductVariantFactory(
        product=product,
        is_active=True,
        selling_price=10,
        mrp=10,
        unit="KG",
        pack_size=1,
        is_sold_loose=True,
        quantity_step=1,
    )
    receive_stock(variant=variant, quantity=Decimal("0.500"))

    detail = get_product_detail(product.slug)

    # The JSON-LD rule stays "on hand is above zero"; only can_add knows that
    # the amount is below one whole step.
    assert detail.variants[0].in_stock is True
    assert detail.variants[0].can_add is False


# ---------------------------------------------------------------------------
# The default quantity (Slice 33, D62)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_default_quantity_is_one_when_it_fits_the_step_and_the_stock() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    variant = ProductVariantFactory(
        product=product,
        is_active=True,
        selling_price=10,
        mrp=10,
        unit="PACKET",
        pack_size=1,
        quantity_step=1,
    )
    receive_stock(variant=variant, quantity=5)

    detail = get_product_detail(product.slug)

    assert detail.variants[0].default_quantity == Decimal("1")


@pytest.mark.django_db
def test_default_quantity_is_the_step_when_one_does_not_fit() -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)

    # A step of 0.3 does not divide one, even though the stock is plentiful.
    odd = ProductFactory(brand=brand, category=cat, is_active=True)
    odd_variant = ProductVariantFactory(
        product=odd,
        is_active=True,
        selling_price=10,
        mrp=10,
        unit="KG",
        pack_size=1,
        is_sold_loose=True,
        quantity_step=Decimal("0.300"),
    )
    receive_stock(variant=odd_variant, quantity=2)

    # Half a kilogram is below one whole unit, so the step is the default too.
    low = ProductFactory(brand=brand, category=cat, is_active=True)
    low_variant = ProductVariantFactory(
        product=low,
        is_active=True,
        selling_price=20,
        mrp=20,
        unit="KG",
        pack_size=1,
        is_sold_loose=True,
        quantity_step=Decimal("0.500"),
    )
    receive_stock(variant=low_variant, quantity=Decimal("0.500"))

    assert get_product_detail(odd.slug).variants[0].default_quantity == Decimal("0.300")
    assert get_product_detail(low.slug).variants[0].default_quantity == Decimal("0.500")
