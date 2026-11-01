"""Product detail service (Slice 25)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import Coalesce

from apps.catalog.models import Product, ProductImage, ProductVariant
from apps.catalog.services import unit_price
from apps.storefront.services import CategoryTree, ProductCard, build_cards, visible_products


@dataclass(frozen=True)
class VariantDetail:
    variant: ProductVariant
    pack_label: str
    on_hand: Decimal
    in_stock: bool
    unit_price_text: str | None


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


def get_product_detail(slug: str) -> ProductDetail:
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
    related = [p for p in related_qs if p.pk != product.pk][:4]

    variant_details: list[VariantDetail] = []
    for variant in variants_list:
        on_hand = getattr(variant, "on_hand", Decimal("0"))
        up_text = None
        if variant.unit in {"KG", "G", "ML", "L"}:
            try:
                up = unit_price(variant.selling_price, variant.pack_size, variant.unit)
                up_text = f"{up.amount} {up.label}"
            except Exception:
                up_text = None
        variant_details.append(
            VariantDetail(
                variant=variant,
                pack_label=_pack_label(variant),
                on_hand=on_hand,
                in_stock=on_hand > 0,
                unit_price_text=up_text,
            )
        )

    images_for_json = []
    main = image_qs[0] if image_qs else None
    if main is not None:
        if main.medium:
            images_for_json.append(main.medium.url)
        elif main.image:
            images_for_json.append(main.image.url)
    for img in image_qs[1:]:
        if img.thumbnail:
            images_for_json.append(img.thumbnail.url)
        elif img.image:
            images_for_json.append(img.image.url)

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
