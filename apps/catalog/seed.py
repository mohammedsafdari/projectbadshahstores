"""Seed catalogue logic (Slice 22, ADR D49).

Thin logic only: no printing, no HTTP. The management command in
``apps/catalog/management/commands/seed_catalog.py`` is the only place that
prints. Everything goes through the existing CSV importer, so the seed uses the
same door as a real billing export.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from pathlib import Path

from django.db import IntegrityError
from django.db.models import QuerySet

from apps.catalog.csv_import_apply import ApplyResult, apply_import
from apps.catalog.models import Brand, Category, ProductVariant
from apps.inventory.models import InventoryMovement
from apps.inventory.services import receive_stock

SEED_DIR = Path(__file__).resolve().parent / "seed_data"


class SeedError(Exception):
    """Raised when a brand or category cannot be created from the seed file."""


def ensure_brands_and_categories(raw: bytes) -> tuple[int, int]:
    """Create the brands and top-level categories named in the CSV.

    Reads the CSV text, collects the distinct (slug, name) pairs for brands and
    categories, and creates each with ``get_or_create``. Existing rows are left
    untouched, so a second run creates nothing. Categories are top level (no
    parent). The importer refuses unknown slugs (D47), which is why this step
    exists.

    An ``IntegrityError`` (for example a name already used by another slug) is
    turned into a ``SeedError`` with a clear message.
    """
    # "utf-8-sig" also strips the byte order mark Excel adds.
    text = raw.decode("utf-8-sig")
    # io.StringIO keeps quoted cells with line breaks intact; splitlines() would
    # cut such a cell in two.
    reader = csv.DictReader(io.StringIO(text, newline=""))
    brand_pairs: set[tuple[str, str]] = set()
    category_pairs: set[tuple[str, str]] = set()
    for row in reader:
        brand_slug = (row.get("brand_slug") or "").strip()
        brand_name = (row.get("brand_name") or "").strip()
        category_slug = (row.get("category_slug") or "").strip()
        category_name = (row.get("category_name") or "").strip()
        if brand_slug:
            brand_pairs.add((brand_slug, brand_name))
        if category_slug:
            category_pairs.add((category_slug, category_name))

    brands_created = 0
    categories_created = 0
    try:
        for slug, name in sorted(brand_pairs):
            _, created = Brand.objects.get_or_create(slug=slug, defaults={"name": name})
            if created:
                brands_created += 1
        for slug, name in sorted(category_pairs):
            _, created = Category.objects.get_or_create(slug=slug, defaults={"name": name})
            if created:
                categories_created += 1
    except IntegrityError as exc:
        raise SeedError(f"Could not create a brand or category: {exc}") from exc
    return brands_created, categories_created


def seed_file(path: Path | str) -> ApplyResult:
    """Seed one CSV file through the importer and return its ``ApplyResult``.

    Brands and categories are created first (idempotent). The file then goes
    through ``parse_variants_csv``, ``build_plan`` and ``apply_import`` inside
    the importer. ``ImportRejected`` is allowed to propagate.
    """
    path = Path(path)
    raw = path.read_bytes()
    ensure_brands_and_categories(raw)
    return apply_import(raw, actor=None, filename=path.name)


def receive_demo_stock() -> int:
    """Receive demo stock once per demo variant that has no movement yet.

    Packed variants get 20, loose variants get 25. Variants that already have
    any inventory movement are skipped. Returns how many variants got stock.
    """
    variants: QuerySet[ProductVariant] = ProductVariant.objects.filter(sku_code__startswith="DEMO-")
    count = 0
    for variant in variants:
        if InventoryMovement.objects.filter(variant_id=variant.pk).exists():
            continue
        quantity: Decimal | int = 25 if variant.is_sold_loose else 20
        receive_stock(variant=variant, quantity=quantity, reason="Demo seed stock")
        count += 1
    return count
