"""CSV import plan: compare the clean rows with the database and write nothing (D47).

This is the second of the three steps described in ``apps/catalog/csv_import.py``:

1. **parse** (``csv_import.py``): bytes in, cleaned and typed rows out, plus every
   problem that the file itself reveals.
2. **plan** (this file): one action per row and per product, plus every problem that
   only the database can reveal (unknown brand, SKU letter-case clash, taken barcode).
   Nothing is written.
3. **apply** (later slice): write everything inside one transaction, using this plan.

The plan is what the dry-run page shows to a person, and what Confirm compares: a
fresh plan with a different ``fingerprint()`` means the file or the database moved on
since the preview, so the person has to look again.

Query budget: five queries for a file of any size. Brands, categories, products,
variants and barcodes are each loaded with one ``__in`` lookup, never one query per row.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# ``Lower`` lives in django.db.models.functions; the models module does not export it.
from django.db.models.functions import Lower

from apps.catalog.csv_import import Issue, ParsedRow, ParseResult
from apps.catalog.models import Brand, Category, Product, ProductVariant

# ---------------------------------------------------------------------------
# The three action words. Plain strings, so the admin page and the tests can
# compare them without importing an enum.
# ---------------------------------------------------------------------------
CREATE = "CREATE"
UPDATE = "UPDATE"
UNCHANGED = "UNCHANGED"

# CSV column -> model field, for the fields that live on the variant. Most columns
# have the same name as the field; the last one differs because the column says which
# object it belongs to (``variant_is_active``) while the model field is just
# ``is_active``.
VARIANT_FIELD_MAP: dict[str, str] = {
    "barcode": "barcode",
    "unit": "unit",
    "pack_size": "pack_size",
    "is_sold_loose": "is_sold_loose",
    "quantity_step": "quantity_step",
    "low_stock_threshold": "low_stock_threshold",
    "hsn_code": "hsn_code",
    "gst_rate_percent": "gst_rate_percent",
    "mrp": "mrp",
    "selling_price": "selling_price",
    "cost_price": "cost_price",
    "net_quantity_label": "net_quantity_label",
    "best_before_label": "best_before_label",
    "shelf_life": "shelf_life",
    "ingredients": "ingredients",
    "allergens": "allergens",
    "manufacturer_details": "manufacturer_details",
    "variant_is_active": "is_active",
}

# CSV column -> model field, for the fields that live on the product. ``brand_slug``
# and ``category_slug`` are missing here on purpose: a slug is text, so it is looked up
# first and then becomes ``brand_id`` / ``category_id`` in the values.
PRODUCT_FIELD_MAP: dict[str, str] = {
    "product_name_en": "name_en",
    "product_name_hi": "name_hi",
    "description": "description",
    "search_keywords": "search_keywords",
    "is_own_brand": "is_own_brand",
    "diet_type": "diet_type",
    "product_is_active": "is_active",
}


# ---------------------------------------------------------------------------
# Results
# ``frozen=True`` makes these objects read-only: a later ``plan.action = ...`` raises
# instead of quietly changing an answer someone is about to show a person. A plan is a
# statement about the database at one moment, so it is never edited after build_plan.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProductPlan:
    """What would happen to one product row (matched by ``product_slug``)."""

    slug: str
    action: str  # CREATE, UPDATE or UNCHANGED
    pk: int | None  # None for a product the import would create
    updated_at: datetime | None  # None for a new product
    brand_pk: int
    category_pk: int
    # Model field -> the new value the file asks for, e.g. {"name_en": "Atta", ...}
    values: dict[str, Any]
    # Only the model fields that differ from the database (all of them for CREATE)
    changed_fields: tuple[str, ...]


@dataclass(frozen=True)
class RowPlan:
    """What would happen to one data row (matched by ``sku_code``)."""

    line: int  # line in the file, so the report can point at it
    sku_code: str
    action: str  # CREATE, UPDATE or UNCHANGED
    variant_pk: int | None  # None for a variant the import would create
    variant_updated_at: datetime | None  # None for a new variant
    product_slug: str
    values: dict[str, Any]  # model field -> the new value the file asks for
    changed_fields: tuple[str, ...]
    product_changed: bool  # True when the row's product is new or its fields changed


@dataclass(frozen=True)
class ImportPlan:
    """The whole answer: one plan per kept row, one per product, and every problem.

    ``rows`` and ``products`` hold only what can be applied. A row with a plan issue
    is left out, so one bad row cannot make the page claim a product will be created
    when nothing of it will be written.
    """

    sha256: str  # hash of the uploaded file, from ParseResult
    row_count: int  # data rows seen in the file, including the rejected ones
    rows: list[RowPlan]
    products: dict[str, ProductPlan]  # slug -> plan, one per product the rows need
    issues: list[Issue]

    @property
    def ok(self) -> bool:
        """True when nothing is wrong: every row can be applied."""
        return not self.issues

    def counts(self) -> dict[str, int]:
        """How many rows are CREATE, UPDATE and UNCHANGED."""
        totals = {CREATE: 0, UPDATE: 0, UNCHANGED: 0}
        for row in self.rows:
            totals[row.action] += 1
        return totals

    def fingerprint(self) -> str:
        """A short text that changes when the file or the compared rows change.

        The same file against the same database gives the same text. A different file,
        or an edit to one of the variants or products in between (which moves its
        ``updated_at``), gives a different text. Confirm compares the two so a preview
        made before somebody else's edit is never applied blindly.
        """
        payload = [
            self.sha256,
            # Rows in line order, so the same file always serialises the same way.
            [
                [
                    row.line,
                    row.action,
                    row.sku_code,
                    row.variant_pk,
                    _iso(row.variant_updated_at),
                    sorted(row.changed_fields),
                ]
                for row in sorted(self.rows, key=lambda row: (row.line, row.sku_code))
            ],
            # Products in slug order, for the same reason.
            [
                [
                    plan.slug,
                    plan.action,
                    plan.pk,
                    _iso(plan.updated_at),
                    sorted(plan.changed_fields),
                ]
                for plan in sorted(self.products.values(), key=lambda plan: plan.slug)
            ],
        ]
        # sort_keys and the compact separators make the JSON text identical on every
        # run. JSON has no datetime type, so timestamps go in as ISO text.
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _iso(value: datetime | None) -> str | None:
    """ISO text for a timestamp, or None for a row that does not exist yet."""
    return None if value is None else value.isoformat()


def _differs(stored: Any, new: Any) -> bool:
    """True when the stored value and the new value are not the same value.

    ``Decimal`` compares by value, so ``Decimal("280") == Decimal("280.00")`` is True
    and a price written with extra zeros counts as unchanged (D47 point 3). ``None``
    equals a NULL barcode, threshold or cost price. Text and flags compare as they are.
    """
    return stored != new


def _variant_values(values: dict[str, Any]) -> dict[str, Any]:
    """Model field -> new value for one variant, in ``VARIANT_FIELD_MAP`` order.

    This is a dict comprehension: ``{field: values[column] for column, field in ...}``
    reads "for every column/field pair in the map, store field -> the cell's value".
    ``product_id`` is not included: a product the file creates has no pk yet, so the
    apply step takes it from the product plan. D47 point 3 guarantees that an existing
    variant keeps the product it already has.
    """
    return {field: values[column] for column, field in VARIANT_FIELD_MAP.items()}


def _product_values(values: dict[str, Any], brand: Brand, category: Category) -> dict[str, Any]:
    """Model field -> new value for one product, with both foreign keys resolved."""
    product_values = {field: values[column] for column, field in PRODUCT_FIELD_MAP.items()}
    # Django's attribute for a foreign key id ends in ``_id``; the model field itself is
    # called ``brand`` / ``category``. The comparison and the eventual save both use the
    # id, so the id is what the plan stores.
    product_values["brand_id"] = brand.pk
    product_values["category_id"] = category.pk
    return product_values


# ---------------------------------------------------------------------------
# The whole file
# ---------------------------------------------------------------------------
def build_plan(parsed: ParseResult) -> ImportPlan:
    """Compare the clean rows with the database and decide what would happen.

    Writes nothing. Every database problem is added to the parse issues and the row it
    belongs to is dropped from the plan, so the dry-run page can show every problem of
    the file in one report.
    """
    issues: list[Issue] = list(parsed.issues)
    rows = parsed.rows

    # Everything the lookups need, collected first, so each lookup stays one query.
    lower_skus = {row.values["sku_code"].lower() for row in rows}
    brand_slugs = {row.values["brand_slug"] for row in rows}
    category_slugs = {row.values["category_slug"] for row in rows}
    product_slugs = {row.values["product_slug"] for row in rows}
    barcodes: set[str] = {
        row.values["barcode"] for row in rows if row.values["barcode"] is not None
    }

    # Query 1 and 2: brands and categories, by exact slug. The dict comprehension
    # {brand.slug: brand for brand in ...} builds a lookup table, "slug -> row", so the
    # loop below can ask ``brands["my-brand"]`` instead of querying again per row.
    brands = {brand.slug: brand for brand in Brand.objects.filter(slug__in=brand_slugs)}
    categories = {cat.slug: cat for cat in Category.objects.filter(slug__in=category_slugs)}

    # Query 3: the products the file mentions, with the fields the comparison needs.
    stored_products: dict[str, Product] = {
        product.slug: product for product in Product.objects.filter(slug__in=product_slugs)
    }

    # Query 4: variants whose SKU matches the file, ignoring letter case. ``Lower`` asks
    # the database to lower-case the column before comparing, so one query finds both
    # the exact matches and the ones that clash only by letter case.
    stored_variants = list(
        ProductVariant.objects.select_related("product")
        .annotate(sku_lower=Lower("sku_code"))
        .filter(sku_lower__in=lower_skus)
    )
    exact_variants: dict[str, ProductVariant] = {}
    variants_by_lower: dict[str, ProductVariant] = {}
    # One name per job: this loop variable is always a saved row, never None.
    for stored_variant in stored_variants:
        exact_variants[stored_variant.sku_code] = stored_variant
        variants_by_lower[stored_variant.sku_code.lower()] = stored_variant

    # Query 5: which SKU already owns each barcode in the file. ``values_list`` returns
    # (barcode, sku_code) pairs, and dict() turns those pairs into a lookup table.
    # The barcode column may be NULL, so skip those pairs and keep a plain str -> str table.
    barcode_owner: dict[str, str] = {
        barcode: sku
        for barcode, sku in ProductVariant.objects.filter(barcode__in=barcodes).values_list(
            "barcode", "sku_code"
        )
        if barcode is not None
    }

    # Rules 2, 3, 5 and 6: problems that need the database. A row with any of them is
    # dropped; the rest go on to be planned.
    kept: list[tuple[ParsedRow, ProductVariant | None]] = []
    for row in rows:
        values = row.values
        sku = values["sku_code"]
        problems_before = len(issues)

        # ``.get()`` can return None, so this name stays apart from the saved-row loop above.
        existing_variant = exact_variants.get(sku)
        if existing_variant is None:
            clash = variants_by_lower.get(sku.lower())
            if clash is not None:
                issues.append(
                    Issue(
                        row.line,
                        "sku_code",
                        f"SKU '{sku}' differs only in letter case from existing SKU "
                        f"'{clash.sku_code}'.",
                    )
                )

        if values["brand_slug"] not in brands:
            issues.append(
                Issue(
                    row.line,
                    "brand_slug",
                    f"Unknown brand '{values['brand_slug']}'. The import does not create "
                    "brands; add it in the admin first.",
                )
            )
        if values["category_slug"] not in categories:
            issues.append(
                Issue(
                    row.line,
                    "category_slug",
                    f"Unknown category '{values['category_slug']}'. The import does not "
                    "create categories; add it in the admin first.",
                )
            )

        if existing_variant is not None and existing_variant.product.slug != values["product_slug"]:
            issues.append(
                Issue(
                    row.line,
                    "product_slug",
                    f"SKU belongs to product '{existing_variant.product.slug}'; moving a "
                    "SKU to another product is not supported.",
                )
            )

        barcode = values["barcode"]
        if barcode is not None:
            owner = barcode_owner.get(barcode)
            # The same SKU keeping its own barcode is fine; another SKU is not, which
            # also catches two SKUs of one file swapping barcodes.
            if owner is not None and owner != sku:
                issues.append(
                    Issue(
                        row.line,
                        "barcode",
                        f"Barcode '{barcode}' already belongs to SKU '{owner}'. Clear it "
                        "there first.",
                    )
                )

        if len(issues) == problems_before:
            kept.append((row, existing_variant))

    # Rule 4: one product plan per slug, built from the rows that had no problem. Many
    # rows share a product, so the first clean row of a slug decides and the rest reuse
    # that plan.
    product_plans: dict[str, ProductPlan] = {}
    for row, _variant in kept:
        values = row.values
        slug = values["product_slug"]
        if slug in product_plans:
            continue
        brand = brands[values["brand_slug"]]
        category = categories[values["category_slug"]]
        new_values = _product_values(values, brand, category)
        stored = stored_products.get(slug)
        if stored is None:
            product_plans[slug] = ProductPlan(
                slug=slug,
                action=CREATE,
                pk=None,
                updated_at=None,
                brand_pk=brand.pk,
                category_pk=category.pk,
                values=new_values,
                # tuple(dict) gives the dict's keys, i.e. every field, in map order.
                changed_fields=tuple(new_values),
            )
            continue
        changed = tuple(
            field for field, new in new_values.items() if _differs(getattr(stored, field), new)
        )
        product_plans[slug] = ProductPlan(
            slug=slug,
            action=UPDATE if changed else UNCHANGED,
            pk=stored.pk,
            updated_at=stored.updated_at,
            brand_pk=stored.brand_id,
            category_pk=stored.category_id,
            values=new_values,
            changed_fields=changed,
        )

    # Rule 7: the row's own action. CREATE for a new variant; UPDATE when a variant
    # field or the row's product differs; UNCHANGED otherwise.
    row_plans: list[RowPlan] = []
    # Same name as above: a saved variant, or None when the import would create it.
    for row, existing_variant in kept:
        values = row.values
        product_plan = product_plans[values["product_slug"]]
        new_values = _variant_values(values)
        product_changed = product_plan.action != UNCHANGED
        if existing_variant is None:
            row_plans.append(
                RowPlan(
                    line=row.line,
                    sku_code=values["sku_code"],
                    action=CREATE,
                    variant_pk=None,
                    variant_updated_at=None,
                    product_slug=product_plan.slug,
                    values=new_values,
                    changed_fields=tuple(new_values),
                    product_changed=True,
                )
            )
            continue
        changed = tuple(
            field
            for field, new in new_values.items()
            if _differs(getattr(existing_variant, field), new)
        )
        row_plans.append(
            RowPlan(
                line=row.line,
                sku_code=values["sku_code"],
                action=UPDATE if changed or product_changed else UNCHANGED,
                variant_pk=existing_variant.pk,
                variant_updated_at=existing_variant.updated_at,
                product_slug=product_plan.slug,
                values=new_values,
                changed_fields=changed,
                product_changed=product_changed,
            )
        )

    # Rule 8: one list of problems, file problems and database problems together,
    # in file order (line, then column).
    return ImportPlan(
        sha256=parsed.sha256,
        row_count=parsed.row_count,
        rows=row_plans,
        products=product_plans,
        issues=sorted(issues, key=lambda issue: (issue.line, issue.column)),
    )
