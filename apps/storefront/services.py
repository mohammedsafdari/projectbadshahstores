"""Storefront business logic (Slice 23c)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Prefetch, Sum
from django.db.models.functions import Coalesce

from apps.catalog.models import Brand, Category, Product, ProductImage, ProductVariant
from apps.catalog.services import savings_percent


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


def _categories() -> list[Category]:
    return list(Category.objects.all())


def category_ancestors(category: Category) -> list[Category]:
    by_id = {c.pk: c for c in _categories()}
    result: list[Category] = []
    visited: set[int] = set()
    current = category.parent_id
    while current is not None and current not in visited:
        visited.add(current)
        ancestor = by_id.get(current)
        if ancestor is None:
            break
        result.insert(0, ancestor)
        current = ancestor.parent_id
    return result


def visible_category_ids() -> set[int]:
    categories = _categories()
    by_id = {c.pk: c for c in categories}
    result: set[int] = set()
    for category in categories:
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
            ancestor = by_id.get(current)
            if ancestor is None or not ancestor.is_active:
                ok = False
                break
            current = ancestor.parent_id
        if ok:
            result.add(category.pk)
    return result


def category_with_descendant_ids(category: Category) -> set[int]:
    categories = _categories()
    children: dict[int | None, list[int]] = {}
    for cat in categories:
        children.setdefault(cat.parent_id, []).append(cat.pk)
    result: set[int] = set()
    visited: set[int] = set()
    stack = [category.pk]
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        result.add(current)
        for child in children.get(current, []):
            if child not in visited:
                stack.append(child)
    return result


def listing_products(
    *,
    category: Category | None = None,
    brand: Brand | None = None,
    sort: str = "name",
) -> list[ProductCard]:
    allowed_sorts = {"name", "price_asc", "price_desc", "newest"}
    if sort not in allowed_sorts:
        sort = "name"

    active_categories = visible_category_ids()

    product_qs = Product.objects.filter(is_active=True)
    product_qs = product_qs.filter(brand__is_active=True)
    product_qs = product_qs.filter(category_id__in=active_categories)

    if category is not None:
        descendant_ids = category_with_descendant_ids(category)
        product_qs = product_qs.filter(category_id__in=descendant_ids)
    if brand is not None:
        product_qs = product_qs.filter(brand=brand)

    product_qs = product_qs.filter(variants__is_active=True).distinct()

    if sort == "newest":
        product_qs = product_qs.order_by("-created_at", "-pk")

    variant_qs = (
        ProductVariant.objects.filter(is_active=True)
        .annotate(on_hand=Coalesce(Sum("movements__quantity_change"), Decimal("0")))
        .order_by("selling_price", "id")
    )
    image_qs = ProductImage.objects.order_by("sort_order", "id")

    products = list(
        product_qs.select_related("brand", "category").prefetch_related(
            Prefetch("variants", queryset=variant_qs, to_attr="active_variants"),
            Prefetch("images", queryset=image_qs, to_attr="sorted_images"),
        )
    )

    cards: list[ProductCard] = []
    for product in products:
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

    if sort == "name":
        cards.sort(key=lambda c: (c.name.lower(), c.slug))
    elif sort == "price_asc":
        cards.sort(key=lambda c: (c.lead_price, c.slug))
    elif sort == "price_desc":
        cards.sort(key=lambda c: c.lead_price, reverse=True)

    return cards
