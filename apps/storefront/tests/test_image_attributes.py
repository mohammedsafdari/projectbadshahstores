"""Image size and decoding attributes (Slice 49, D90).

A photo with no width and height makes the page jump while it loads, and an
async decode keeps a large photo off the main thread. Each test parses one
specific <img> tag, never the whole page.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductImageFactory,
    ProductVariantFactory,
)
from apps.core.models import ShopSettings
from apps.inventory.tests.factories import InventoryMovementFactory
from apps.promotions.tests.helpers import make_banner

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row():
    cache.clear()
    ShopSettings.load()
    yield
    cache.clear()


def _product(*, name: str = "Image Item", images: int = 1, stock: str = "10.000"):
    """One visible product with photos and stock."""
    brand = BrandFactory(is_active=True)
    category = CategoryFactory(is_active=True)
    product = ProductFactory(name_en=name, brand=brand, category=category, is_active=True)
    variant = ProductVariantFactory(
        product=product,
        is_active=True,
        unit=ProductVariant.Unit.PACKET,
        pack_size=Decimal("1.000"),
    )
    InventoryMovementFactory(variant=variant, quantity_change=Decimal(stock))
    for position in range(images):
        ProductImageFactory(product=product, sort_order=position)
    return product


def _img_with_class(html: str, class_name: str) -> str:
    """The first <img> tag whose class is exactly this one."""
    for tag in re.findall(r"<img\b[^>]*>", html):
        if f'class="{class_name}"' in tag:
            return tag
    raise AssertionError(f"No <img> with class {class_name}.")


def test_card_images_have_size_and_async_decoding() -> None:
    _product()
    html = Client().get(reverse("product_list")).content.decode()

    tag = _img_with_class(html, "h-full w-full object-contain")

    assert 'width="400"' in tag
    assert 'height="400"' in tag
    assert 'decoding="async"' in tag
    assert 'loading="lazy"' in tag


def test_banner_image_has_size_and_async_decoding() -> None:
    make_banner()
    html = Client().get(reverse("home")).content.decode()

    tag = _img_with_class(html, "h-full w-full object-cover")

    assert 'width="800"' in tag
    assert 'height="400"' in tag
    assert 'decoding="async"' in tag


def test_detail_main_image_stays_eager() -> None:
    product = _product(images=1)
    html = Client().get(reverse("product_detail", args=[product.slug])).content.decode()

    tag = _img_with_class(html, "w-full max-w-md")

    assert 'loading="eager"' in tag
    assert 'decoding="async"' in tag
    assert 'width="900"' in tag
    assert 'height="900"' in tag


def test_detail_gallery_images_are_lazy() -> None:
    product = _product(images=3)
    html = Client().get(reverse("product_detail", args=[product.slug])).content.decode()

    tag = _img_with_class(html, "h-16 w-16 object-contain")

    assert 'loading="lazy"' in tag
    assert 'decoding="async"' in tag
    assert 'width="64"' in tag
    assert 'height="64"' in tag


def test_suggestion_images_keep_size_and_lazy() -> None:
    _product(name="Suggest Item")

    html = Client().get(reverse("search_suggest"), {"q": "suggest"}).content.decode()

    tag = _img_with_class(html, "h-10 w-10 object-contain")

    assert 'width="40"' in tag
    assert 'height="40"' in tag
    assert 'loading="lazy"' in tag
    assert 'decoding="async"' in tag
