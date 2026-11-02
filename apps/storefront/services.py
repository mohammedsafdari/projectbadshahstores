"""Storefront business logic (Slice 23c, refactored in 23f)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from django.db import models
from django.db.models import (
    Case,
    IntegerField,
    Min,
    Prefetch,
    Q,
    Sum,
    Value,
    When,
    prefetch_related_objects,
)
from django.db.models.functions import Coalesce, Lower

from apps.catalog.models import Brand, Category, Product, ProductImage, ProductVariant
from apps.catalog.services import savings_percent
from apps.storefront.filters import ListingFilters, apply_filters


@dataclass(frozen=True)
class ProductCard:
    name: str
    slug: str
    brand_name: str
    lead_price: Decimal
    mrp: Decimal
    percent_off: int
    has_more: bool
    in_stock: bool
    image_url: str | None
    alt_text: str


class CategoryTree:
    def __init__(self, categories: Iterable[Category] | None = None) -> None:
        if categories is None:
            categories = Category.objects.all()
        self._categories = list(categories)
        self._by_id = {c.pk: c for c in self._categories}
        self._children: dict[int | None, list[int]] = {}
        for cat in self._categories:
            self._children.setdefault(cat.parent_id, []).append(cat.pk)

    def ancestors(self, category: Category) -> list[Category]:
        result: list[Category] = []
        visited: set[int] = set()
        current = category.parent_id
        while current is not None and current not in visited:
            visited.add(current)
            ancestor = self._by_id.get(current)
            if ancestor is None:
                break
            result.insert(0, ancestor)
            current = ancestor.parent_id
        return result

    def visible_ids(self) -> set[int]:
        result: set[int] = set()
        for category in self._categories:
            if not category.is_active:
                continue
            ok = True
            visited: set[int] = set()
            current = category.parent_id
            while current is not None:
                if current in visited:
                    ok = False
                    break
                visited.add(current)
                ancestor = self._by_id.get(current)
                if ancestor is None or not ancestor.is_active:
                    ok = False
                    break
                current = ancestor.parent_id
            if ok:
                result.add(category.pk)
        return result

    def descendant_ids(self, category: Category) -> set[int]:
        result: set[int] = set()
        visited: set[int] = set()
        stack = [category.pk]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            result.add(current)
            for child in self._children.get(current, []):
                if child not in visited:
                    stack.append(child)
        return result


def visible_products(
    *,
    tree: CategoryTree,
    category: Category | None = None,
    brand: Brand | None = None,
    sort: str = "name",
    filters: ListingFilters | None = None,
    product_ids: Iterable[int] | None = None,
) -> models.QuerySet[Product]:
    allowed_sorts = {"name", "price_asc", "price_desc", "newest", "relevance"}
    if sort not in allowed_sorts:
        sort = "name"

    # A search backend returns ids best first. Keep that order for the database
    # sort below; None means "no id restriction" and [] means "no matches".
    ranked_ids: list[int] | None = None
    if product_ids is not None:
        ranked_ids = list(product_ids)

    active_categories = tree.visible_ids()

    qs = Product.objects.filter(is_active=True, brand__is_active=True).select_related(
        "brand", "category"
    )
    qs = qs.filter(category_id__in=active_categories)
    if ranked_ids is not None:
        qs = qs.filter(pk__in=ranked_ids)

    if category is not None:
        descendant_ids = tree.descendant_ids(category)
        qs = qs.filter(category_id__in=descendant_ids)
    if brand is not None:
        qs = qs.filter(brand=brand)

    qs = qs.annotate(lead_price=Min("variants__selling_price", filter=Q(variants__is_active=True)))
    qs = qs.filter(lead_price__isnull=False)
    if filters is not None:
        qs = apply_filters(qs, filters)

    if sort == "relevance" and ranked_ids:
        # Case/When gives each product the position of its id in the search
        # result, so the database sorts by relevance without loading rows into
        # Python. There are at most SEARCH_CANDIDATE_LIMIT ids.
        relevance_order = Case(
            *(When(pk=pk, then=Value(position)) for position, pk in enumerate(ranked_ids)),
            output_field=IntegerField(),
        )
        qs = qs.order_by(relevance_order)
    elif sort in {"name", "relevance"}:
        # Relevance without ids has nothing to rank, so it falls back to the name.
        qs = qs.order_by(Lower("name_en"), "pk")
    elif sort == "price_asc":
        qs = qs.order_by("lead_price", "pk")
    elif sort == "price_desc":
        qs = qs.order_by("-lead_price", "pk")
    elif sort == "newest":
        qs = qs.order_by("-created_at", "-pk")

    return qs


def build_cards(products: Iterable[Product]) -> list[ProductCard]:
    product_list = list(products)

    variant_qs = (
        ProductVariant.objects.filter(is_active=True)
        .annotate(on_hand=Coalesce(Sum("movements__quantity_change"), Decimal("0")))
        .order_by("selling_price", "id")
    )
    image_qs = ProductImage.objects.order_by("sort_order", "id")

    prefetch_related_objects(
        product_list,
        Prefetch("variants", queryset=variant_qs, to_attr="active_variants"),
        Prefetch("images", queryset=image_qs, to_attr="sorted_images"),
    )

    cards: list[ProductCard] = []
    for product in product_list:
        variants = getattr(product, "active_variants", [])
        if not variants:
            continue
        lead = variants[0]
        has_more = len(variants) > 1
        in_stock = any(getattr(v, "on_hand", Decimal("0")) > 0 for v in variants)
        images = getattr(product, "sorted_images", [])
        main_image = images[0] if images else None
        image_url = None
        alt_text = product.name_en
        if main_image is not None:
            alt_text = main_image.alt_text or product.name_en
            if main_image.thumbnail:
                image_url = main_image.thumbnail.url
            elif main_image.image:
                image_url = main_image.image.url
        percent = 0
        if lead.mrp > lead.selling_price:
            percent = int(savings_percent(lead.mrp, lead.selling_price))
        cards.append(
            ProductCard(
                name=product.name_en,
                slug=product.slug,
                brand_name=product.brand.name,
                lead_price=lead.selling_price,
                mrp=lead.mrp,
                percent_off=percent,
                has_more=has_more,
                in_stock=in_stock,
                image_url=image_url,
                alt_text=alt_text,
            )
        )
    return cards
