"""CSV export of catalog variants (Slice 19, ADR D46).

One CSV row is one variant (one SKU), with the details of its product, brand and
category on the same row. The column list below is the single place that decides
what the file looks like; the CSV import (Slice 20) reads the same columns, so a
file exported here can be edited and imported back.

This module does no HTTP work. It writes into any file-like object, so the admin
passes a response, and tests pass an ``io.StringIO``.

REVIEW CAREFULLY: the file contains cost prices (our margins) and is opened by
people in Excel. Two rules protect that, see ``_safe_text`` and ``write_variants_csv``.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from decimal import Decimal
from typing import Any, Protocol

from django.db.models import QuerySet

from apps.catalog.models import ProductVariant


class SupportsWrite(Protocol):
    """Anything with a ``write(text)`` method: an open file, a StringIO or an HttpResponse.

    A Protocol describes a shape instead of a class: any object that has this
    method fits, with no inheritance needed. mypy uses it to check the calls.
    """

    def write(self, s: str, /) -> object: ...


# Every column in the file, in order. ``TEXT_COLUMNS`` are cleaned by
# ``_safe_text``; the rest are numbers or TRUE/FALSE flags we format ourselves.
CSV_COLUMNS: tuple[str, ...] = (
    "sku_code",
    "barcode",
    "product_slug",
    "product_name_en",
    "product_name_hi",
    "brand_slug",
    "brand_name",
    "category_slug",
    "category_name",
    "is_own_brand",
    "diet_type",
    "description",
    "search_keywords",
    "unit",
    "pack_size",
    "is_sold_loose",
    "quantity_step",
    "low_stock_threshold",
    "hsn_code",
    "gst_rate_percent",
    "mrp",
    "selling_price",
    "cost_price",
    "net_quantity_label",
    "best_before_label",
    "shelf_life",
    "ingredients",
    "allergens",
    "manufacturer_details",
    "product_is_active",
    "variant_is_active",
)

# A frozenset (a set that cannot change) of the columns that hold free text.
TEXT_COLUMNS: frozenset[str] = frozenset(
    {
        "sku_code",
        "barcode",
        "product_slug",
        "product_name_en",
        "product_name_hi",
        "brand_slug",
        "brand_name",
        "category_slug",
        "category_name",
        "diet_type",
        "description",
        "search_keywords",
        "unit",
        "hsn_code",
        "net_quantity_label",
        "best_before_label",
        "shelf_life",
        "ingredients",
        "allergens",
        "manufacturer_details",
    }
)

# Excel and Google Sheets treat a cell that starts with one of these as a formula.
_FORMULA_STARTERS = ("=", "+", "-", "@", "\t", "\r")

# The first character of the file. Excel on Windows uses it to recognise UTF-8,
# without it Hindi (Devanagari) text shows up as garbage.
_UTF8_BOM = "\ufeff"


def _safe_text(value: str) -> str:
    """Stop a text cell from being run as a spreadsheet formula (CSV injection).

    A name such as ``=HYPERLINK("http://evil", "click")`` would execute when
    someone opens the file in Excel. Putting a single quote in front makes the
    spreadsheet treat the cell as plain text. The Slice 20 import must remove
    that leading quote again.
    """
    if value.startswith(_FORMULA_STARTERS):
        return "'" + value
    return value


def _number(value: Decimal | None) -> str:
    """A Decimal as plain digits, e.g. ``95.00``. A missing value is an empty cell."""
    if value is None:
        return ""
    # format(x, "f") never switches to scientific notation such as 1E+2.
    return format(value, "f")


def _flag(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def variants_for_export(variants: QuerySet[ProductVariant]) -> QuerySet[ProductVariant]:
    """Sort the variants by SKU and load their product, brand and category in one query.

    ``select_related`` joins the related tables, so writing 1,000 rows costs
    one query, not 3,000.
    """
    return variants.select_related("product__brand", "product__category").order_by("sku_code")


def variant_to_row(variant: ProductVariant) -> list[str]:
    """Turn one variant into the list of cell values, in ``CSV_COLUMNS`` order."""
    product = variant.product
    values: dict[str, Any] = {
        "sku_code": variant.sku_code,
        "barcode": variant.barcode or "",
        "product_slug": product.slug,
        "product_name_en": product.name_en,
        "product_name_hi": product.name_hi,
        "brand_slug": product.brand.slug,
        "brand_name": product.brand.name,
        "category_slug": product.category.slug,
        "category_name": product.category.name,
        "is_own_brand": _flag(product.is_own_brand),
        "diet_type": product.diet_type,
        "description": product.description,
        "search_keywords": product.search_keywords,
        "unit": variant.unit,
        "pack_size": _number(variant.pack_size),
        "is_sold_loose": _flag(variant.is_sold_loose),
        "quantity_step": _number(variant.quantity_step),
        "low_stock_threshold": _number(variant.low_stock_threshold),
        "hsn_code": variant.hsn_code,
        "gst_rate_percent": _number(variant.gst_rate_percent),
        "mrp": _number(variant.mrp),
        "selling_price": _number(variant.selling_price),
        "cost_price": _number(variant.cost_price),
        "net_quantity_label": variant.net_quantity_label,
        "best_before_label": variant.best_before_label,
        "shelf_life": variant.shelf_life,
        "ingredients": variant.ingredients,
        "allergens": variant.allergens,
        "manufacturer_details": variant.manufacturer_details,
        "product_is_active": _flag(product.is_active),
        "variant_is_active": _flag(variant.is_active),
    }
    # Look each column up by name, so the row can never get out of step with
    # CSV_COLUMNS. Only the text columns need the formula guard.
    return [
        _safe_text(values[name]) if name in TEXT_COLUMNS else values[name] for name in CSV_COLUMNS
    ]


def write_variants_csv(variants: Iterable[ProductVariant], file: SupportsWrite) -> int:
    """Write the header and one row per variant to ``file``. Return the row count.

    The file starts with a UTF-8 byte order mark (see ``_UTF8_BOM``), so a reader
    in Python must open it with ``encoding="utf-8-sig"``.

    Pass a queryset prepared with ``variants_for_export``; a plain queryset still
    works but runs extra queries for each row.
    """
    file.write(_UTF8_BOM)
    writer = csv.writer(file)
    writer.writerow(CSV_COLUMNS)
    count = 0
    for variant in variants:
        writer.writerow(variant_to_row(variant))
        count += 1
    return count
