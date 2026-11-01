"""Listing filters: parse the query string and apply it (Slice 24a)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db import models
from django.db.models import Exists, OuterRef, Sum
from django.db.models.functions import Coalesce
from django.http import QueryDict

from apps.catalog.models import Brand, Product, ProductVariant

MAX_PRICE = Decimal("1000000")


@dataclass(frozen=True)
class ListingFilters:
    """The filters a customer can apply to the listing pages."""

    brand_slugs: tuple[str, ...] = ()
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    own_brand: bool = False
    in_stock: bool = False
    veg_only: bool = False

    @property
    def is_active(self) -> bool:
        """True when at least one filter is set (used for the Clear filters link)."""
        return bool(
            self.brand_slugs
            or self.min_price is not None
            or self.max_price is not None
            or self.own_brand
            or self.in_stock
            or self.veg_only
        )


def _parse_price(text: str) -> Decimal | None:
    """Return a valid price, or None when the text is bad or out of range."""
    text = text.strip()
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    # is_finite() must be checked before comparing, because comparing NaN raises.
    if not value.is_finite():
        return None
    if value < 0 or value > MAX_PRICE:
        return None
    return value


def _parse_checkbox(value: str) -> bool:
    """True only for the accepted truthy values."""
    return value.strip().lower() in {"1", "on", "true"}


def parse_filters(params: QueryDict) -> ListingFilters:
    """Read the filters from the query string. Bad input is ignored, never an error."""
    # getlist returns every value for a key; strip and drop blanks and duplicates.
    brand_slugs_list: list[str] = []
    seen: set[str] = set()
    for raw in params.getlist("brand"):
        value = raw.strip()
        if value and value not in seen:
            seen.add(value)
            brand_slugs_list.append(value)

    min_price = _parse_price(params.get("min_price", ""))
    max_price = _parse_price(params.get("max_price", ""))

    # Put the smaller value first when both are set.
    if min_price is not None and max_price is not None and min_price > max_price:
        min_price, max_price = max_price, min_price

    return ListingFilters(
        brand_slugs=tuple(brand_slugs_list),
        min_price=min_price,
        max_price=max_price,
        own_brand=_parse_checkbox(params.get("own_brand", "")),
        in_stock=_parse_checkbox(params.get("in_stock", "")),
        veg_only=_parse_checkbox(params.get("veg", "")),
    )


def apply_filters(
    qs: models.QuerySet[Product], filters: ListingFilters
) -> models.QuerySet[Product]:
    """Apply the filters in the database. Called after lead_price is annotated."""
    if filters.brand_slugs:
        known = list(
            Brand.objects.filter(is_active=True, slug__in=filters.brand_slugs).values_list(
                "slug", flat=True
            )
        )
        if known:
            qs = qs.filter(brand__slug__in=known)

    if filters.min_price is not None:
        qs = qs.filter(lead_price__gte=filters.min_price)  # type: ignore[misc]
    if filters.max_price is not None:
        qs = qs.filter(lead_price__lte=filters.max_price)  # type: ignore[misc]
    if filters.own_brand:
        qs = qs.filter(is_own_brand=True)
    if filters.veg_only:
        qs = qs.filter(diet_type=Product.DietType.VEG)
    if filters.in_stock:
        # OuterRef("pk") means "the product of the outer query"; Exists keeps one
        # row per product, so no duplicates appear.
        stocked_variants = (
            ProductVariant.objects.filter(product=OuterRef("pk"), is_active=True)
            .annotate(on_hand=Coalesce(Sum("movements__quantity_change"), Decimal("0")))
            .filter(on_hand__gt=0)
        )
        qs = qs.filter(Exists(stocked_variants))

    return qs
