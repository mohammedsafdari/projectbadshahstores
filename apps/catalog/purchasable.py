"""Which variants the storefront may sell (D50, D60).

The rule is the same visibility rule the listing pages use: the variant, its
product and its brand are active, and its category and every ancestor category
are active. It lives here, in catalog, so ``cart`` can use it without importing
``storefront`` (storefront imports cart later).

The query count is constant: one for the variants with their product, brand and
category flags, and one for the category table (which is small).
"""

from __future__ import annotations

from collections.abc import Iterable

from apps.catalog.models import Category, ProductVariant


def purchasable_variant_ids(variant_ids: Iterable[int]) -> set[int]:
    """Return the ids that pass the storefront visibility rule.

    A category loop in the data is walked with a visited set, so it can never
    hang the caller; a loop hides the variant because the chain cannot be
    proven active.
    """
    ids = set(variant_ids)
    if not ids:
        return set()

    rows = ProductVariant.objects.filter(pk__in=ids).values_list(
        "pk",
        "is_active",
        "product__is_active",
        "product__brand__is_active",
        "product__category_id",
    )

    # One read of the whole category table: there are only hundreds of rows.
    categories: dict[int, tuple[bool, int | None]] = {
        pk: (is_active, parent_id)
        for pk, is_active, parent_id in Category.objects.values_list("pk", "is_active", "parent_id")
    }

    result: set[int] = set()
    for pk, variant_active, product_active, brand_active, category_id in rows:
        if not (variant_active and product_active and brand_active):
            continue
        ok = True
        visited: set[int] = set()
        current: int | None = category_id
        while current is not None:
            if current in visited:
                ok = False
                break
            visited.add(current)
            row = categories.get(current)
            if row is None or not row[0]:
                ok = False
                break
            current = row[1]
        if ok:
            result.add(pk)
    return result
