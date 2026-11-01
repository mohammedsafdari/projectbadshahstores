"""Choices shown in the filter panel (Slice 24a)."""

from __future__ import annotations

from django.db.models.functions import Lower

from apps.catalog.models import Brand, Category
from apps.storefront.services import CategoryTree, visible_products


def brand_options(*, tree: CategoryTree, category: Category | None = None) -> list[Brand]:
    """Brands that have at least one visible product in this scope."""
    scope = visible_products(tree=tree, category=category)
    return list(Brand.objects.filter(pk__in=scope.values("brand_id")).order_by(Lower("name"), "pk"))
