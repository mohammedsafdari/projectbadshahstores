"""Export then import: a file the shop exported must come back unchanged (D46/D47).

The export (Slice 19) and the import (Slice 20) share one column list, so a file
written by ``write_variants_csv`` must plan as UNCHANGED for every row. These tests
close that loop with real database rows, including the cases the file format is
fragile about: a loose item, a missing barcode, a name that looks like a formula and
Hindi text.
"""

import csv
import io
from decimal import Decimal

import pytest

from apps.catalog.audit import VARIANT_UPDATE_ACTION
from apps.catalog.csv_export import variants_for_export, write_variants_csv
from apps.catalog.csv_import_apply import apply_import
from apps.catalog.models import Product, ProductVariant
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.core.models import AuditLog
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def variants() -> list[ProductVariant]:
    """Five variants of one product, each covering a case the roundtrip can break."""
    brand = BrandFactory(slug="aashirvaad", name="Aashirvaad")
    category = CategoryFactory(slug="atta", name="Atta")
    product = ProductFactory(
        slug="chakki-atta",
        # Leading "=": the export must guard it, the import must undo that guard.
        name_en="=Chakki Atta",
        name_hi="चक्की आटा",
        description="Whole wheat flour",
        search_keywords="atta flour",
        diet_type=Product.DietType.VEG,
        brand=brand,
        category=category,
    )
    return [
        ProductVariantFactory(
            product=product,
            sku_code="ATTA-1KG",
            barcode="8901234567890",
            pack_size=Decimal("1.000"),
            low_stock_threshold=Decimal("5.000"),
            cost_price=Decimal("40.00"),
        ),
        # No barcode: the export writes an empty cell, which must read back as NULL.
        ProductVariantFactory(
            product=product,
            sku_code="ATTA-5KG",
            barcode=None,
            pack_size=Decimal("5.000"),
        ),
        ProductVariantFactory(
            product=product,
            sku_code="ATTA-10KG",
            barcode="8901234567891",
            pack_size=Decimal("10.000"),
        ),
        # A loose item: unit KG, pack size 1 and a fractional step.
        ProductVariantFactory(
            product=product,
            sku_code="ATTA-LOOSE",
            barcode="8901234567892",
            unit=ProductVariant.Unit.KG,
            is_sold_loose=True,
            pack_size=Decimal("1.000"),
            quantity_step=Decimal("0.100"),
        ),
        # An archived variant: both is_active flags must survive the roundtrip.
        ProductVariantFactory(
            product=product,
            sku_code="ATTA-2KG",
            barcode="8901234567893",
            pack_size=Decimal("2.000"),
            is_active=False,
        ),
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _export_text() -> str:
    """Write every variant the way the admin export does, into memory."""
    buffer = io.StringIO()
    write_variants_csv(variants_for_export(ProductVariant.objects.all()), buffer)
    return buffer.getvalue()


def _rows(text: str) -> list[list[str]]:
    """The exported text as rows. ``lstrip`` drops the byte order mark character."""
    return list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))


def _encode(rows: list[list[str]], delimiter: str = ",") -> bytes:
    """Rows back to UTF-8 bytes, with the comma, semicolon or tab the import accepts."""
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=delimiter).writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _with_price(rows: list[list[str]], sku: str, price: str) -> list[list[str]]:
    """Change the selling_price cell of one row, as a person editing the file would."""
    header = rows[0]
    sku_at = header.index("sku_code")
    price_at = header.index("selling_price")
    for row in rows[1:]:
        if row[sku_at] == sku:
            row[price_at] = price
    return rows


# ---------------------------------------------------------------------------
# The roundtrip
# ---------------------------------------------------------------------------
def test_export_then_import_changes_nothing(variants) -> None:
    actor = UserFactory()
    raw = _encode(_rows(_export_text()))

    result = apply_import(raw, actor=actor, filename="export.csv")

    assert (result.created, result.updated, result.unchanged) == (0, 0, 5)
    assert result.products_created == 0
    assert result.products_updated == 0
    # No variant changed, so nothing may be audited.
    assert not AuditLog.objects.exists()


def test_roundtrip_with_a_semicolon_delimiter_changes_nothing(variants) -> None:
    actor = UserFactory()
    # The export always writes commas; Excel on a German-locale PC writes semicolons,
    # so the same rows are re-written with ";" before the import sees them.
    raw = _encode(_rows(_export_text()), delimiter=";")

    result = apply_import(raw, actor=actor, filename="export.csv")

    assert (result.created, result.updated, result.unchanged) == (0, 0, 5)
    assert not AuditLog.objects.exists()


def test_one_price_change_writes_one_update_and_one_audit_row(variants) -> None:
    actor = UserFactory()
    rows = _with_price(_rows(_export_text()), "ATTA-5KG", "90.00")

    result = apply_import(_encode(rows), actor=actor, filename="export.csv")

    assert (result.created, result.updated, result.unchanged) == (0, 1, 4)
    audit_row = AuditLog.objects.get()
    assert audit_row.action == VARIANT_UPDATE_ACTION
    assert audit_row.changes == {"selling_price": ["95.00", "90.00"]}
    assert ProductVariant.objects.get(sku_code="ATTA-5KG").selling_price == Decimal("90.00")
