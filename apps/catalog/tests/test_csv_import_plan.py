"""Tests for the CSV import plan (Slice 20c, ADR D47).

The plan compares parsed rows with the database and writes nothing. Each test starts
from the one valid row of the parser tests (``VALID_ROW``) and changes only the cell it
is about, so a database problem shows up on its own.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.catalog.csv_import import parse_variants_csv
from apps.catalog.csv_import_plan import (
    CREATE,
    PRODUCT_FIELD_MAP,
    UNCHANGED,
    UPDATE,
    VARIANT_FIELD_MAP,
    ImportPlan,
    build_plan,
)
from apps.catalog.models import Brand, Category, ProductVariant
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.catalog.tests.test_csv_import_parse import VALID_ROW, make_csv

pytestmark = pytest.mark.django_db

SKU = VALID_ROW["sku_code"]
PRODUCT_SLUG = VALID_ROW["product_slug"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def plan_for(*rows: dict[str, str]) -> ImportPlan:
    """Build a file from ``rows``, parse it and plan it. Nothing is written."""
    return build_plan(parse_variants_csv(make_csv(*rows)))


@pytest.fixture
def catalog() -> tuple[Brand, Category]:
    """The brand and category VALID_ROW names; the import never creates them."""
    brand = BrandFactory(slug=VALID_ROW["brand_slug"], name="Aashirvaad")
    category = CategoryFactory(slug=VALID_ROW["category_slug"], name="Atta")
    return brand, category


def stored_catalog(catalog: tuple[Brand, Category], **variant_changes: Any) -> ProductVariant:
    """Store the product and variant that VALID_ROW describes.

    ``variant_changes`` overrides variant fields, so a test can store a slightly
    different row (no barcode, no cost price, another price) without repeating the rest.
    """
    brand, category = catalog
    product = ProductFactory(
        slug=PRODUCT_SLUG,
        name_en=VALID_ROW["product_name_en"],
        name_hi=VALID_ROW["product_name_hi"],
        description=VALID_ROW["description"],
        search_keywords=VALID_ROW["search_keywords"],
        diet_type=VALID_ROW["diet_type"],
        brand=brand,
        category=category,
    )
    fields: dict[str, Any] = {
        "product": product,
        "sku_code": SKU,
        "barcode": VALID_ROW["barcode"],
        "pack_size": Decimal(VALID_ROW["pack_size"]),
        "low_stock_threshold": Decimal(VALID_ROW["low_stock_threshold"]),
        "mrp": Decimal(VALID_ROW["mrp"]),
        "selling_price": Decimal(VALID_ROW["selling_price"]),
        "cost_price": Decimal(VALID_ROW["cost_price"]),
        "net_quantity_label": VALID_ROW["net_quantity_label"],
        "best_before_label": VALID_ROW["best_before_label"],
        "shelf_life": VALID_ROW["shelf_life"],
        "ingredients": VALID_ROW["ingredients"],
        "allergens": VALID_ROW["allergens"],
        "manufacturer_details": VALID_ROW["manufacturer_details"],
    }
    # One dict, then the overrides: naming a key here and again in ** would pass it twice.
    fields.update(variant_changes)
    return ProductVariantFactory(**fields)


# ---------------------------------------------------------------------------
# CREATE, UPDATE, UNCHANGED
# ---------------------------------------------------------------------------
def test_one_row_in_an_empty_catalog_is_create(catalog) -> None:
    plan = plan_for({})
    assert plan.ok
    assert plan.counts() == {CREATE: 1, UPDATE: 0, UNCHANGED: 0}
    row = plan.rows[0]
    assert row.action == CREATE
    assert row.variant_pk is None
    assert row.variant_updated_at is None
    assert row.product_changed is True
    # A new row has no stored values to compare with, so every field counts as changed.
    assert row.changed_fields == tuple(VARIANT_FIELD_MAP.values())
    assert set(row.values) == set(VARIANT_FIELD_MAP.values())
    product = plan.products[PRODUCT_SLUG]
    assert product.action == CREATE
    assert product.pk is None
    assert set(product.changed_fields) == set(PRODUCT_FIELD_MAP.values()) | {
        "brand_id",
        "category_id",
    }


def test_same_values_are_unchanged(catalog) -> None:
    stored_catalog(catalog)
    plan = plan_for({})
    assert plan.ok
    assert plan.rows[0].action == UNCHANGED
    assert plan.rows[0].changed_fields == ()
    assert plan.products[PRODUCT_SLUG].action == UNCHANGED


def test_price_written_without_decimals_is_unchanged(catalog) -> None:
    stored_catalog(catalog)
    plan = plan_for({"mrp": "280", "selling_price": "265"})
    assert plan.ok
    assert plan.rows[0].action == UNCHANGED


def test_blank_barcode_equals_null(catalog) -> None:
    stored_catalog(catalog, barcode=None)
    plan = plan_for({"barcode": ""})
    assert plan.ok
    assert plan.rows[0].action == UNCHANGED


def test_blank_cost_price_equals_null(catalog) -> None:
    stored_catalog(catalog, cost_price=None)
    plan = plan_for({"cost_price": ""})
    assert plan.ok
    assert plan.rows[0].action == UNCHANGED


def test_a_sku_may_keep_its_own_barcode(catalog) -> None:
    stored_catalog(catalog)
    plan = plan_for({})
    assert plan.issues == []


def test_new_selling_price_is_an_update(catalog) -> None:
    stored_catalog(catalog)
    plan = plan_for({"selling_price": "250.00"})
    row = plan.rows[0]
    assert row.action == UPDATE
    assert row.changed_fields == ("selling_price",)
    assert row.values["selling_price"] == Decimal("250.00")


def test_product_name_change_marks_the_row_update(catalog) -> None:
    stored_catalog(catalog)
    plan = plan_for({"product_name_en": "Chakki Atta Premium"})
    row = plan.rows[0]
    assert row.action == UPDATE
    assert row.changed_fields == ()  # no variant field changed
    assert row.product_changed is True
    assert plan.products[PRODUCT_SLUG].changed_fields == ("name_en",)


# ---------------------------------------------------------------------------
# Problems that need the database
# ---------------------------------------------------------------------------
def test_unknown_brand_is_an_issue(catalog) -> None:
    plan = plan_for({"brand_slug": "no-such-brand"})
    assert [issue.column for issue in plan.issues] == ["brand_slug"]
    assert "Unknown brand 'no-such-brand'" in plan.issues[0].message
    assert plan.rows == []


def test_unknown_category_is_an_issue(catalog) -> None:
    plan = plan_for({"category_slug": "no-such-category"})
    assert [issue.column for issue in plan.issues] == ["category_slug"]
    assert "Unknown category 'no-such-category'" in plan.issues[0].message
    assert plan.rows == []


def test_sku_differing_only_in_letter_case_is_an_issue(catalog) -> None:
    stored_catalog(catalog)
    # The blank barcode keeps the row free of a second problem.
    plan = plan_for({"sku_code": "atta-5kg", "barcode": ""})
    assert [issue.column for issue in plan.issues] == ["sku_code"]
    assert "differs only in letter case" in plan.issues[0].message
    assert plan.rows == []


def test_existing_sku_under_another_product_is_an_issue(catalog) -> None:
    stored_catalog(catalog)
    plan = plan_for({"product_slug": "other-atta"})
    assert [issue.column for issue in plan.issues] == ["product_slug"]
    assert "moving a SKU to another product is not supported" in plan.issues[0].message
    assert plan.rows == []
    assert plan.products == {}


def test_barcode_of_another_sku_is_an_issue(catalog) -> None:
    variant = stored_catalog(catalog, barcode=None)
    # Another SKU of the same product now holds the barcode VALID_ROW asks for.
    ProductVariantFactory(
        product=variant.product,
        sku_code="ATTA-10KG",
        barcode=VALID_ROW["barcode"],
    )
    plan = plan_for({})
    assert [issue.column for issue in plan.issues] == ["barcode"]
    assert "already belongs to SKU 'ATTA-10KG'" in plan.issues[0].message
    assert plan.rows == []


def test_two_skus_swapping_barcodes_are_both_issues(catalog) -> None:
    first = stored_catalog(catalog)
    ProductVariantFactory(
        product=first.product,
        sku_code="ATTA-10KG",
        barcode="1111111111111",
    )
    plan = plan_for(
        {"sku_code": SKU, "barcode": "1111111111111"},
        {"sku_code": "ATTA-10KG", "barcode": VALID_ROW["barcode"], "pack_size": "10.000"},
    )
    barcode_issues = [issue for issue in plan.issues if issue.column == "barcode"]
    assert [issue.line for issue in barcode_issues] == [2, 3]
    assert all("already belongs to SKU" in issue.message for issue in barcode_issues)
    assert plan.rows == []


# ---------------------------------------------------------------------------
# Products, issues and the report
# ---------------------------------------------------------------------------
def test_three_variants_of_one_new_product(catalog) -> None:
    plan = plan_for(
        {"sku_code": "ATTA-1KG", "barcode": "", "pack_size": "1.000"},
        {"sku_code": "ATTA-5KG", "barcode": "", "pack_size": "5.000"},
        {"sku_code": "ATTA-10KG", "barcode": "", "pack_size": "10.000"},
    )
    assert plan.ok
    assert [row.line for row in plan.rows] == [2, 3, 4]
    assert all(row.action == CREATE for row in plan.rows)
    # Many rows, one product: one plan for the slug, not one per row.
    assert len(plan.products) == 1
    assert plan.products[PRODUCT_SLUG].action == CREATE


def test_parse_and_plan_issues_are_merged_in_line_order(catalog) -> None:
    plan = plan_for(
        {"mrp": "abc"},
        {"sku_code": "ATTA-1KG", "barcode": "", "brand_slug": "no-such-brand"},
    )
    assert [(issue.line, issue.column) for issue in plan.issues] == [
        (2, "mrp"),
        (3, "brand_slug"),
    ]
    assert plan.rows == []


# ---------------------------------------------------------------------------
# Query budget and the promise not to write
# ---------------------------------------------------------------------------
def _capture(*rows: dict[str, str]) -> CaptureQueriesContext:
    """Plan ``rows`` and return the queries Django ran while doing it."""
    with CaptureQueriesContext(connection) as captured:
        plan_for(*rows)
    return captured


def test_query_count_does_not_grow_with_the_file(catalog) -> None:
    one_row = _capture({"sku_code": "ATTA-1KG", "barcode": ""})
    many_rows = _capture(
        *({"sku_code": f"ATTA-{n}KG", "barcode": "", "pack_size": "1.000"} for n in range(40))
    )
    assert len(one_row.captured_queries) <= 6
    assert len(many_rows.captured_queries) == len(one_row.captured_queries)


def test_plan_writes_nothing(catalog) -> None:
    stored_catalog(catalog)
    captured = _capture({"selling_price": "250.00"})
    statements = [
        query["sql"].lstrip().split(None, 1)[0].upper() for query in captured.captured_queries
    ]
    assert statements  # the plan did read the database
    assert not {"INSERT", "UPDATE", "DELETE"}.intersection(statements)


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------
def test_fingerprint_is_the_same_for_the_same_input(catalog) -> None:
    stored_catalog(catalog)
    assert plan_for({}).fingerprint() == plan_for({}).fingerprint()


def test_fingerprint_changes_after_a_variant_is_saved(catalog) -> None:
    variant = stored_catalog(catalog)
    before = plan_for({}).fingerprint()
    # save() refreshes updated_at, which the fingerprint carries.
    variant.save()
    assert plan_for({}).fingerprint() != before


def test_fingerprint_changes_for_another_file(catalog) -> None:
    stored_catalog(catalog)
    assert plan_for({}).fingerprint() != plan_for({"mrp": "281.00"}).fingerprint()
