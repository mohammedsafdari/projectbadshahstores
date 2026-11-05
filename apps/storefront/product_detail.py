"""Product detail service (Slice 25)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import Coalesce

from apps.cart.presentation import unit_label
from apps.cart.rules import MAX_LOOSE_PER_LINE, MAX_PACKETS_PER_LINE
from apps.catalog.models import Product, ProductImage, ProductVariant
from apps.catalog.services import savings_amount, savings_percent, unit_price
from apps.inventory.availability import get_available_quantities
from apps.storefront.services import CategoryTree, ProductCard, build_cards, visible_products

# The three preset buttons a loose item offers when they fit its step and stock.
LOOSE_PRESETS = (Decimal("0.25"), Decimal("0.5"), Decimal("1"))


@dataclass(frozen=True)
class VariantDetail:
    variant: ProductVariant
    pack_label: str
    on_hand: Decimal
    in_stock: bool
    unit_price_text: str | None
    savings_amount: Decimal
    savings_percent: int
    available: Decimal
    can_add: bool
    is_loose: bool
    unit_label: str
    presets: list[Decimal]
    default_quantity: Decimal
    max_quantity: Decimal


@dataclass(frozen=True)
class ProductDetail:
    product: Product
    variants: list[VariantDetail]
    images: list[ProductImage]
    related: list[Product]
    related_cards: list[ProductCard]
    json_ld: str


def _pack_label(variant: ProductVariant) -> str:
    if variant.net_quantity_label:
        return variant.net_quantity_label
    size = variant.pack_size
    unit = variant.unit
    if size == 1:
        return f"1 {unit.lower()}"
    return f"{size:g} {unit.lower()}"


def _presets(variant: ProductVariant, available: Decimal) -> list[Decimal]:
    """Only presets that are a multiple of the step and fit the stock."""
    if not variant.is_sold_loose:
        return []
    # Decimal remainder is exact because both values have at most 3 decimals.
    return [
        preset
        for preset in LOOSE_PRESETS
        if preset % variant.quantity_step == 0 and preset <= available
    ]


def _default_quantity(variant: ProductVariant, available: Decimal) -> Decimal:
    """One when the shop has it and the step divides it, otherwise one step."""
    one = Decimal("1")
    step = variant.quantity_step
    if available >= one and one % step == 0:
        return one
    return step


def get_product_detail(slug: str, build_url=None) -> ProductDetail:
    if build_url is None:

        def build_url(path: str) -> str:
            return path

    tree = CategoryTree()
    qs = visible_products(tree=tree, sort="name")
    product = qs.filter(slug=slug).first()
    if product is None:
        raise Product.DoesNotExist

    variant_qs = (
        ProductVariant.objects.filter(product=product, is_active=True)
        .annotate(on_hand=Coalesce(Sum("movements__quantity_change"), Decimal("0")))
        .order_by("selling_price", "id")
    )
    variants_list = list(variant_qs)

    image_qs = list(ProductImage.objects.filter(product=product).order_by("sort_order", "id"))

    related_qs = visible_products(tree=tree, category=product.category, sort="name")
    related = list(related_qs.exclude(pk=product.pk)[:4])

    # One grouped query for every variant; the number of queries stays the
    # same whether the product has one pack size or ten (D61).
    available_by_id = get_available_quantities([variant.pk for variant in variants_list])

    variant_details: list[VariantDetail] = []
    for variant in variants_list:
        on_hand = getattr(variant, "on_hand", Decimal("0"))
        available = available_by_id.get(variant.pk, Decimal("0"))
        up_text = None
        if variant.unit in {"KG", "G", "ML", "L"}:
            up = unit_price(variant.selling_price, variant.pack_size, variant.unit)
            up_text = f"{up.amount} {up.label}"
        save_amt = (
            savings_amount(variant.mrp, variant.selling_price)
            if variant.mrp > variant.selling_price
            else Decimal("0")
        )
        save_pct = (
            int(savings_percent(variant.mrp, variant.selling_price))
            if variant.mrp > variant.selling_price
            else 0
        )
        cap = MAX_LOOSE_PER_LINE if variant.is_sold_loose else Decimal(MAX_PACKETS_PER_LINE)
        variant_details.append(
            VariantDetail(
                variant=variant,
                pack_label=_pack_label(variant),
                on_hand=on_hand,
                # in_stock stays the ledger rule the JSON-LD already uses; the
                # add button also respects the online stock buffer (can_add).
                in_stock=on_hand > 0,
                unit_price_text=up_text,
                savings_amount=save_amt,
                savings_percent=save_pct,
                available=available,
                can_add=available >= variant.quantity_step,
                is_loose=variant.is_sold_loose,
                unit_label=unit_label(variant),
                presets=_presets(variant, available),
                default_quantity=_default_quantity(variant, available),
                max_quantity=Decimal(cap),
            )
        )

    images_for_json = []
    main = image_qs[0] if image_qs else None
    if main is not None:
        if main.medium:
            images_for_json.append(build_url(main.medium.url))
        elif main.image:
            images_for_json.append(build_url(main.image.url))
    for img in image_qs[1:]:
        if img.thumbnail:
            images_for_json.append(build_url(img.thumbnail.url))
        elif img.image:
            images_for_json.append(build_url(img.image.url))

    prices = [v.selling_price for v in variants_list]
    json_ld_obj: dict[str, object] = {
        "name": product.name_en,
        "description": product.description,
        "image": images_for_json,
        "brand": {"@type": "Brand", "name": product.brand.name},
        "sku": variants_list[0].sku_code if variants_list else None,
    }
    if variants_list and variants_list[0].barcode:
        json_ld_obj["gtin"] = variants_list[0].barcode
    if len(variants_list) == 1:
        v = variants_list[0]
        json_ld_obj["offers"] = {
            "@type": "Offer",
            "price": f"{v.selling_price:.2f}",
            "priceCurrency": "INR",
            "availability": (
                "https://schema.org/InStock"
                if variant_details[0].in_stock
                else "https://schema.org/OutOfStock"
            ),
        }
    else:
        json_ld_obj["offers"] = {
            "@type": "AggregateOffer",
            "lowPrice": f"{min(prices):.2f}",
            "highPrice": f"{max(prices):.2f}",
            "offerCount": len(variants_list),
            "priceCurrency": "INR",
            "availability": (
                "https://schema.org/InStock"
                if any(vd.in_stock for vd in variant_details)
                else "https://schema.org/OutOfStock"
            ),
        }
    json_ld_obj["@context"] = "https://schema.org"
    json_ld_obj["@type"] = "Product"
    # safe for <script>
    dumped = json.dumps(json_ld_obj)
    dumped = dumped.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    json_ld = dumped

    return ProductDetail(
        product=product,
        variants=variant_details,
        images=image_qs,
        related=related,
        related_cards=build_cards(related),
        json_ld=json_ld,
    )
