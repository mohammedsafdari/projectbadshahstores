"""Tests for the CSV export service (Slice 19)."""

import csv
import io
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.catalog.csv_export import (
    CSV_COLUMNS,
    TEXT_COLUMNS,
    variants_for_export,
    write_variants_csv,
)
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)

pytestmark = pytest.mark.django_db


def _export() -> str:
    """Export every variant to text, the way the admin does."""
    buffer = io.StringIO()
    write_variants_csv(variants_for_export(ProductVariant.objects.all()), buffer)
    return buffer.getvalue()


def _rows(text: str) -> list[dict[str, str]]:
    """Read the exported text back with the csv module, one dict per data row."""
    # "utf-8-sig" is how a real file is read; here the BOM is the first character.
    assert text.startswith("\ufeff")
    return list(csv.DictReader(io.StringIO(text.removeprefix("\ufeff"))))


def test_every_text_column_is_a_real_column() -> None:
    assert TEXT_COLUMNS.issubset(CSV_COLUMNS)
    assert len(set(CSV_COLUMNS)) == len(CSV_COLUMNS)


def test_empty_export_has_bom_and_header_only() -> None:
    text = _export()
    assert text.startswith("\ufeff")
    assert _rows(text) == []
    header = text.removeprefix("\ufeff").splitlines()[0]
    assert header.split(",") == list(CSV_COLUMNS)


def test_returns_the_number_of_rows_written() -> None:
    ProductVariantFactory.create_batch(3)
    buffer = io.StringIO()
    count = write_variants_csv(variants_for_export(ProductVariant.objects.all()), buffer)
    assert count == 3


def test_row_values_for_a_full_variant() -> None:
    product = ProductFactory(
        name_en="Sharbati Atta",
        name_hi="शरबती आटा",
        slug="sharbati-atta",
        brand=BrandFactory(name="Badshah Spices", slug="badshah-spices"),
        category=CategoryFactory(name="Flour", slug="flour"),
        is_own_brand=True,
        diet_type="VEG",
        search_keywords="atta aata",
    )
    ProductVariantFactory(
        product=product,
        sku_code="ATTA-5KG",
        barcode="8901234567890",
        unit="KG",
        pack_size=Decimal("5"),
        hsn_code="1101",
        gst_rate_percent=Decimal("5"),
        mrp=Decimal("300"),
        selling_price=Decimal("285.5"),
        cost_price=Decimal("250"),
    )
    (row,) = _rows(_export())
    assert row["sku_code"] == "ATTA-5KG"
    assert row["barcode"] == "8901234567890"
    assert row["product_slug"] == "sharbati-atta"
    assert row["product_name_en"] == "Sharbati Atta"
    assert row["product_name_hi"] == "शरबती आटा"
    assert row["brand_slug"] == "badshah-spices"
    assert row["brand_name"] == "Badshah Spices"
    assert row["category_slug"] == "flour"
    assert row["category_name"] == "Flour"
    assert row["is_own_brand"] == "TRUE"
    assert row["diet_type"] == "VEG"
    assert row["search_keywords"] == "atta aata"
    assert row["unit"] == "KG"
    assert row["is_sold_loose"] == "FALSE"
    assert row["hsn_code"] == "1101"
    assert row["product_is_active"] == "TRUE"
    assert row["variant_is_active"] == "TRUE"


def test_numbers_keep_the_stored_decimal_places() -> None:
    # Read the variant back from the database so the values have the scale
    # (number of decimal places) that PostgreSQL stores, not what the test typed.
    ProductVariantFactory(
        pack_size=Decimal("5"),
        quantity_step=Decimal("1"),
        gst_rate_percent=Decimal("5"),
        mrp=Decimal("300"),
        selling_price=Decimal("285.5"),
        cost_price=Decimal("250"),
        low_stock_threshold=Decimal("0"),
    )
    (row,) = _rows(_export())
    assert row["pack_size"] == "5.000"
    assert row["quantity_step"] == "1.000"
    assert row["gst_rate_percent"] == "5.00"
    assert row["mrp"] == "300.00"
    assert row["selling_price"] == "285.50"
    assert row["cost_price"] == "250.00"
    # A threshold of zero is a real value and must not look like "blank".
    assert row["low_stock_threshold"] == "0.000"


def test_missing_optional_values_are_empty_cells() -> None:
    ProductVariantFactory(barcode=None, cost_price=None, low_stock_threshold=None)
    (row,) = _rows(_export())
    assert row["barcode"] == ""
    assert row["cost_price"] == ""
    assert row["low_stock_threshold"] == ""


def test_loose_variant_is_flagged() -> None:
    ProductVariantFactory(
        is_sold_loose=True,
        unit="KG",
        pack_size=Decimal("1"),
        quantity_step=Decimal("0.1"),
    )
    (row,) = _rows(_export())
    assert row["is_sold_loose"] == "TRUE"
    assert row["quantity_step"] == "0.100"


def test_archived_variant_and_product_are_exported_as_false() -> None:
    product = ProductFactory(is_active=False)
    ProductVariantFactory(product=product, is_active=False)
    (row,) = _rows(_export())
    assert row["product_is_active"] == "FALSE"
    assert row["variant_is_active"] == "FALSE"


def test_rows_are_sorted_by_sku_code() -> None:
    for sku in ("C-3", "A-1", "B-2"):
        ProductVariantFactory(sku_code=sku)
    assert [row["sku_code"] for row in _rows(_export())] == ["A-1", "B-2", "C-3"]


def test_commas_quotes_and_new_lines_survive_a_round_trip() -> None:
    nasty = 'Garam masala, "special" blend\nsecond line'
    ProductVariantFactory(product=ProductFactory(description=nasty))
    (row,) = _rows(_export())
    assert row["description"] == nasty


@pytest.mark.parametrize("start", ["=", "+", "-", "@"])
def test_text_that_looks_like_a_formula_is_neutralised(start: str) -> None:
    ProductVariantFactory(product=ProductFactory(name_en=f'{start}HYPERLINK("http://x","y")'))
    (row,) = _rows(_export())
    assert row["product_name_en"] == f'\'{start}HYPERLINK("http://x","y")'


def test_text_with_a_formula_character_inside_is_left_alone() -> None:
    ProductVariantFactory(product=ProductFactory(name_en="Tea 2+1 pack = value"))
    (row,) = _rows(_export())
    assert row["product_name_en"] == "Tea 2+1 pack = value"


def test_numbers_are_never_prefixed() -> None:
    # Only the text columns get the guard; money never starts with "-" or "+".
    ProductVariantFactory(cost_price=Decimal("10"))
    (row,) = _rows(_export())
    assert row["cost_price"] == "10.00"


def test_query_count_does_not_grow_with_the_number_of_variants() -> None:
    ProductVariantFactory.create_batch(2)
    with CaptureQueriesContext(connection) as small:
        _export()
    ProductVariantFactory.create_batch(8)
    with CaptureQueriesContext(connection) as large:
        _export()
    assert len(large) == len(small)
