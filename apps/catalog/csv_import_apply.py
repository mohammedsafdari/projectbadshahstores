"""CSV import, step three: write the plan inside one transaction (Slice 20c, D47).

This module is the only place the import writes rows. It refuses the whole file if
anything is wrong, because a half-applied file is worse than none (D47 point 5).

The order is fixed and matters:

1. lock the products named in the file, then the variants, each in ascending ``pk``
   order (D43 point 2), so two imports that overlap wait instead of deadlocking.
2. plan again with the locks held, so the plan is built from the latest data.
3. write products, then variants, in file order. Every row is validated with
   ``full_clean()`` and every variant change writes its AuditLog row (D44, D47 point 6).

``bulk_create`` and ``queryset.update()`` are deliberately not used: they skip
``full_clean()`` and the audit call, which is exactly what this step must not skip.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from django.contrib.auth.base_user import AbstractBaseUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.catalog.audit import (
    record_variant_change,
    record_variant_created,
    snapshot_variant_from_db,
)
from apps.catalog.csv_import import Issue, ParseResult, parse_variants_csv
from apps.catalog.csv_import_plan import CREATE, UNCHANGED, UPDATE, build_plan
from apps.catalog.models import Product, ProductVariant


# ---------------------------------------------------------------------------
# Results and refusals
# ---------------------------------------------------------------------------
class ImportRejected(Exception):
    """Raised when the import refuses the file. Nothing was written.

    ``issues`` has the same shape as the dry-run report, so the page can show the
    reason with the code it already has.
    """

    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        super().__init__(f"The import was rejected: {len(issues)} problem(s).")


class PlanChanged(ImportRejected):
    """Raised when the file or the database changed since the preview."""


@dataclass(frozen=True)
class ApplyResult:
    """What one successful import did. The counts are rows and products, not audits."""

    created: int
    updated: int
    unchanged: int
    products_created: int
    products_updated: int
    sha256: str


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _note(filename: str) -> str:
    """The audit note: ``CSV import: <file name>``, cut to 200 characters.

    Control characters (a newline in a pasted name, for example) are removed:
    ``unicodedata.category`` returns "Cc" for them, the same test the parser uses.
    """
    text = "CSV import: " + "".join(char for char in filename if unicodedata.category(char) != "Cc")
    return text.strip()[:200]


def _error_text(exc: Exception) -> str:
    """One short line from a validation or database error, for the report."""
    text = " ".join(str(exc).split())
    if len(text) > 200:
        return text[:197] + "..."
    return text


def _lock(parsed: ParseResult) -> tuple[dict[str, Product], dict[str, ProductVariant]]:
    """Lock the stored products and variants the file mentions, in ``pk`` order.

    Must run inside ``transaction.atomic()``: ``select_for_update()`` only means
    something inside a transaction. ``list()`` matters because a queryset is lazy;
    the lock is taken when the rows are actually read, not when the queryset is built.

    Products are locked before variants every time (D43 point 2), and each query
    orders by ``pk``, so two imports of overlapping files take their locks in the
    same order and cannot deadlock each other.

    The returned dicts are keyed the way the plan refers to the rows (slug, SKU) and
    hold the locked instances, so the writes below reuse them instead of reading
    the same rows again.
    """
    product_slugs = {row.values["product_slug"] for row in parsed.rows}
    sku_codes = {row.values["sku_code"] for row in parsed.rows}

    locked_products = list(
        Product.objects.select_for_update().filter(slug__in=product_slugs).order_by("pk")
    )
    locked_variants = list(
        ProductVariant.objects.select_for_update().filter(sku_code__in=sku_codes).order_by("pk")
    )

    return (
        {product.slug: product for product in locked_products},
        {variant.sku_code: variant for variant in locked_variants},
    )


# ---------------------------------------------------------------------------
# The whole import
# ---------------------------------------------------------------------------
def apply_import(
    raw: bytes,
    *,
    actor: AbstractBaseUser | None,
    filename: str,
    expected_fingerprint: str | None = None,
) -> ApplyResult:
    """Write everything the file asks for, or nothing at all.

    ``actor`` is the logged-in user, and goes into every audit row (None for a
    system run). ``expected_fingerprint`` is the fingerprint the person saw on the
    dry-run page; when it is given and the fresh plan differs, nothing is written.
    """
    parsed = parse_variants_csv(raw)

    # A file-level problem (no header, too large, not UTF-8, ...) means there is
    # nothing to lock or plan, so refuse it before a transaction is even opened.
    # Row-level problems are handled below, so the person still sees every one of them.
    if not parsed.ok and not parsed.rows and any(issue.line == 0 for issue in parsed.issues):
        raise ImportRejected(parsed.issues)

    # ``current_line`` is a one-item list, not an int: a list is mutable, so the
    # helper can update it and the except block below can read where the error came
    # from. Reassigning a plain int inside the helper would only change its own copy.
    current_line = [0]
    try:
        # The try is OUTSIDE atomic on purpose. Catching a database error inside
        # the block leaves the transaction marked as broken, so every later query
        # would fail. After the block exits, the transaction has already rolled back.
        with transaction.atomic():
            return _apply_locked(
                parsed,
                actor=actor,
                note=_note(filename),
                expected_fingerprint=expected_fingerprint,
                current_line=current_line,
            )
    except (ValidationError, IntegrityError) as exc:
        raise ImportRejected([Issue(current_line[0], "", _error_text(exc))]) from exc


def _apply_locked(
    parsed: ParseResult,
    *,
    actor: AbstractBaseUser | None,
    note: str,
    expected_fingerprint: str | None,
    current_line: list[int],
) -> ApplyResult:
    """Do the work of ``apply_import`` with the locks held. Call it inside ``atomic``."""
    locked_products, locked_variants = _lock(parsed)

    # Plan now, not before the locks: another import may have committed while this
    # request waited for its lock, and the plan must describe what is stored now.
    plan = build_plan(parsed)
    if not plan.ok:
        raise ImportRejected(plan.issues)
    if expected_fingerprint is not None and expected_fingerprint != plan.fingerprint():
        raise PlanChanged(
            [Issue(0, "", "The data changed since the preview. Upload the file again.")]
        )

    # For an error raised while writing a product, report the first file line that
    # mentions that product. ``setdefault`` keeps the earliest line and does nothing
    # on the repeat rows of the same product.
    first_line: dict[str, int] = {}
    for row in plan.rows:
        first_line.setdefault(row.product_slug, row.line)

    # ---- Products first: a new variant needs its product to exist -----------
    products_created = 0
    products_updated = 0
    products_by_slug = dict(locked_products)
    for slug, product_plan in plan.products.items():
        current_line[0] = first_line.get(slug, 0)
        if product_plan.action == CREATE:
            # ``slug`` is the key the plan matched on, so it is not inside ``values``;
            # a new row still needs it, or ``full_clean()`` rejects the empty slug.
            product = Product(slug=product_plan.slug, **product_plan.values)
            product.full_clean()  # validators, unique fields and the check constraints
            product.save()
            products_by_slug[slug] = product
            products_created += 1
            continue
        if product_plan.action == UNCHANGED:
            continue
        product = products_by_slug[slug]
        for field in product_plan.changed_fields:
            setattr(product, field, product_plan.values[field])
        product.full_clean()
        # update_fields writes only the named columns, so updated_at must be in the
        # list: with update_fields Django does not add auto_now fields by itself.
        product.save(update_fields=[*product_plan.changed_fields, "updated_at"])
        products_updated += 1

    # ---- Then the variants, in file order ----------------------------------
    created = 0
    updated = 0
    unchanged = 0
    variants_by_sku = dict(locked_variants)
    for row in plan.rows:
        current_line[0] = row.line

        if row.action == CREATE:
            product = products_by_slug[row.product_slug]
            # ``**row.values`` unpacks the dict into keyword arguments, so each
            # model field name in the plan becomes one constructor argument. The SKU is
            # the key the plan matched on, so it is not inside ``values`` and is passed
            # here as well.
            variant = ProductVariant(product=product, sku_code=row.sku_code, **row.values)
            variant.full_clean()
            variant.save()
            record_variant_created(actor=actor, variant=variant, note=note)
            variants_by_sku[row.sku_code] = variant
            created += 1
            continue

        variant = variants_by_sku[row.sku_code]

        if row.action == UPDATE and row.changed_fields:
            # Take the old values BEFORE touching the instance: the audit row needs
            # both sides of the change, and only the database still holds the old one.
            old = snapshot_variant_from_db(variant.pk)
            for field in row.changed_fields:
                setattr(variant, field, row.values[field])
            variant.full_clean()
            variant.save(update_fields=[*row.changed_fields, "updated_at"])
            record_variant_change(actor=actor, variant=variant, old=old, note=note)
            updated += 1
            continue

        if row.action == UPDATE:
            # Only the product changed. The variant row itself is not touched, and
            # products are not audited (D47 point 6), so there is no audit row here.
            updated += 1
            continue

        unchanged += 1

    return ApplyResult(
        created=created,
        updated=updated,
        unchanged=unchanged,
        products_created=products_created,
        products_updated=products_updated,
        sha256=plan.sha256,
    )
