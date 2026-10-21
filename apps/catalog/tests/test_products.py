"""Tests for the catalog Product and ProductVariant models."""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError

from apps.catalog.models import Product, ProductVariant

from .factories import BrandFactory, ProductFactory, ProductVariantFactory

# Every test below touches the database.
pytestmark = pytest.mark.django_db


def test_product_defaults() -> None:
    product = ProductFactory()
    assert product.is_own_brand is False
    assert product.diet_type == Product.DietType.NOT_APPLICABLE
    assert product.is_active is True
    assert product.name_hi == ""


def test_product_str_is_english_name() -> None:
    assert str(ProductFactory(name_en="Wheat Atta")) == "Wheat Atta"


def test_hindi_name_is_stored_correctly() -> None:
    product = ProductFactory(name_hi="आटा")
    product.refresh_from_db()
    assert product.name_hi == "आटा"


def test_products_are_ordered_by_english_name() -> None:
    ProductFactory(name_en="Zed", slug="zed")
    ProductFactory(name_en="Alpha", slug="alpha")
    assert [p.name_en for p in Product.objects.all()] == ["Alpha", "Zed"]


def test_product_slug_must_be_unique() -> None:
    ProductFactory(slug="atta")
    with pytest.raises(IntegrityError):
        ProductFactory(slug="atta")


def test_brand_with_products_cannot_be_deleted() -> None:
    brand = BrandFactory()
    ProductFactory(brand=brand)
    with pytest.raises(ProtectedError):
        brand.delete()


def test_variants_are_reachable_from_product() -> None:
    product = ProductFactory()
    variant = ProductVariantFactory(product=product)
    assert list(product.variants.all()) == [variant]


def test_variant_str_is_sku_code() -> None:
    assert str(ProductVariantFactory(sku_code="ATTA-5KG")) == "ATTA-5KG"


def test_variant_sku_code_must_be_unique() -> None:
    ProductVariantFactory(sku_code="ATTA-5KG")
    with pytest.raises(IntegrityError):
        ProductVariantFactory(sku_code="ATTA-5KG")


def test_variants_may_share_a_missing_barcode() -> None:
    ProductVariantFactory(barcode=None)
    ProductVariantFactory(barcode=None)
    assert ProductVariant.objects.filter(barcode__isnull=True).count() == 2


def test_variant_barcode_must_be_unique_when_set() -> None:
    ProductVariantFactory(barcode="8901234567890")
    with pytest.raises(IntegrityError):
        ProductVariantFactory(barcode="8901234567890")


def test_selling_price_cannot_exceed_mrp() -> None:
    with pytest.raises(IntegrityError):
        ProductVariantFactory(mrp=Decimal("100.00"), selling_price=Decimal("101.00"))


def test_selling_price_may_equal_mrp() -> None:
    variant = ProductVariantFactory(
        mrp=Decimal("100.00"),
        selling_price=Decimal("100.00"),
    )
    assert variant.selling_price == variant.mrp


@pytest.mark.parametrize(
    "overrides",
    [
        {"mrp": Decimal("0.00"), "selling_price": Decimal("0.00")},
        {"selling_price": Decimal("0.00")},
    ],
)
def test_prices_must_be_positive(overrides: dict[str, Decimal]) -> None:
    with pytest.raises(IntegrityError):
        ProductVariantFactory(**overrides)


def test_negative_cost_price_is_rejected() -> None:
    with pytest.raises(IntegrityError):
        ProductVariantFactory(cost_price=Decimal("-1.00"))


def test_missing_cost_price_is_allowed() -> None:
    assert ProductVariantFactory(cost_price=None).cost_price is None


def test_gst_rate_above_100_is_rejected_by_database() -> None:
    with pytest.raises(IntegrityError):
        ProductVariantFactory(gst_rate_percent=Decimal("101.00"))


def test_gst_rate_above_100_is_rejected_by_validation() -> None:
    product = ProductFactory()
    variant = ProductVariantFactory.build(
        product=product,
        gst_rate_percent=Decimal("101.00"),
    )
    with pytest.raises(ValidationError) as excinfo:
        variant.full_clean()
    assert "gst_rate_percent" in excinfo.value.message_dict


def test_hsn_code_must_be_digits() -> None:
    product = ProductFactory()
    variant = ProductVariantFactory.build(product=product, hsn_code="12ab")
    with pytest.raises(ValidationError) as excinfo:
        variant.full_clean()
    assert "hsn_code" in excinfo.value.message_dict


def test_valid_variant_passes_full_clean() -> None:
    product = ProductFactory()
    variant = ProductVariantFactory.build(product=product)
    variant.full_clean()


def test_own_brand_label_fields_default_to_blank() -> None:
    variant = ProductVariantFactory()
    assert variant.net_quantity_label == ""
    assert variant.best_before_label == ""
    assert variant.ingredients == ""
    assert variant.allergens == ""
    assert variant.shelf_life == ""
    assert variant.manufacturer_details == ""
