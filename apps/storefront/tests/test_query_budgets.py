"""Query budgets for the public storefront pages (Slice 49, D90).

Each budget is the count measured while building this slice plus two, so a new
N+1 or a repeated query fails the test while a harmless extra query does not.
Every page also has a second test showing the count does not grow with the
data, which is what actually catches a per-row query.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.cart.models import Cart
from apps.cart.tests.factories import CartFactory, CartItemFactory
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
from apps.promotions.models import Banner, FeaturedProduct
from apps.promotions.tests.helpers import png_bytes
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

# measured + 2, per page.
HOME_QUERY_BUDGET = 10
PRODUCT_LIST_QUERY_BUDGET = 9
CATEGORY_PAGE_QUERY_BUDGET = 11
BRAND_PAGE_QUERY_BUDGET = 9
PRODUCT_DETAIL_QUERY_BUDGET = 11
SEARCH_QUERY_BUDGET = 15
SEARCH_SUGGEST_QUERY_BUDGET = 7
CART_QUERY_BUDGET = 13
SITEMAP_QUERY_BUDGET = 6


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """One settings row and an empty cache, so no test sees a stale cache."""
    cache.clear()
    ShopSettings.load()
    yield
    cache.clear()


def _product(
    *,
    brand,
    category,
    name: str,
    variants: int = 1,
    images: int = 1,
    stock: str = "10.000",
):
    """One visible product with its variants, stock and photos."""
    product = ProductFactory(name_en=name, brand=brand, category=category, is_active=True)
    for _ in range(variants):
        variant = ProductVariantFactory(
            product=product,
            is_active=True,
            unit=ProductVariant.Unit.PACKET,
            pack_size=Decimal("1.000"),
            selling_price=Decimal("100.00"),
            mrp=Decimal("120.00"),
        )
        InventoryMovementFactory(variant=variant, quantity_change=Decimal(stock))
    for position in range(images):
        ProductImageFactory(product=product, sort_order=position)
    return product


def _catalogue(products: int = 3, *, variants: int = 1, images: int = 1):
    """One brand and one category holding the requested number of products."""
    brand = BrandFactory(is_active=True)
    category = CategoryFactory(is_active=True)
    rows = [
        _product(
            brand=brand,
            category=category,
            name=f"Budget Item {number}",
            variants=variants,
            images=images,
        )
        for number in range(products)
    ]
    return brand, category, rows


def _banner(title: str) -> Banner:
    """One active banner with a real image, so width and height are filled."""
    return Banner.objects.create(
        title=title,
        image=SimpleUploadedFile("banner.png", png_bytes(800, 400), content_type="image/png"),
        alt_text=f"{title} banner",
    )


def _featured(products) -> None:
    """Feature these products in the order given, as staff would."""
    for position, product in enumerate(products):
        FeaturedProduct.objects.create(product=product, sort_order=position)


def _count(client: Client, url: str, data: dict[str, str] | None = None) -> int:
    """The number of queries one GET of this page runs, which must answer 200.

    The cache is emptied first, so the shop settings are read again on every
    measured request and two measurements always start from the same place.
    """
    cache.clear()
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url, data or {})
    assert response.status_code == 200
    return len(queries)


def _assert_budget(count: int, budget: int) -> None:
    assert count <= budget, f"{count} queries, budget {budget}"


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------
def test_home_query_budget(client: Client) -> None:
    _brand, _category, products = _catalogue(products=3)
    _banner("First")
    _banner("Second")
    _featured(products)

    count = _count(client, reverse("home"))

    _assert_budget(count, HOME_QUERY_BUDGET)


def test_home_queries_do_not_grow(client: Client) -> None:
    _brand, _category, products = _catalogue(products=3)
    _featured(products)

    small = _count(client, reverse("home"))

    extra = [
        _product(
            brand=products[0].brand,
            category=products[0].category,
            name=f"Budget Item {number}",
        )
        for number in range(3, 20)
    ]
    _featured(extra)
    large = _count(client, reverse("home"))

    assert large == small


# ---------------------------------------------------------------------------
# Product list, category page and brand page
# ---------------------------------------------------------------------------
def test_product_list_query_budget(client: Client) -> None:
    _catalogue(products=3)

    count = _count(client, reverse("product_list"))

    _assert_budget(count, PRODUCT_LIST_QUERY_BUDGET)


def test_product_list_queries_do_not_grow(client: Client) -> None:
    _brand, category, products = _catalogue(products=3)

    small = _count(client, reverse("product_list"))

    for number in range(3, 20):
        _product(
            brand=products[0].brand,
            category=category,
            name=f"Budget Item {number}",
        )
    large = _count(client, reverse("product_list"))

    assert large == small


def test_category_page_query_budget(client: Client) -> None:
    _brand, category, _products = _catalogue(products=3)

    count = _count(client, reverse("category_detail", args=[category.slug]))

    _assert_budget(count, CATEGORY_PAGE_QUERY_BUDGET)


def test_category_page_queries_do_not_grow(client: Client) -> None:
    _brand, category, products = _catalogue(products=3)
    url = reverse("category_detail", args=[category.slug])

    small = _count(client, url)

    for number in range(3, 20):
        _product(
            brand=products[0].brand,
            category=category,
            name=f"Budget Item {number}",
        )
    large = _count(client, url)

    assert large == small


def test_brand_page_query_budget(client: Client) -> None:
    brand, _category, _products = _catalogue(products=3)

    count = _count(client, reverse("brand_detail", args=[brand.slug]))

    _assert_budget(count, BRAND_PAGE_QUERY_BUDGET)


def test_brand_page_queries_do_not_grow(client: Client) -> None:
    brand, category, _products = _catalogue(products=3)
    url = reverse("brand_detail", args=[brand.slug])

    small = _count(client, url)

    for number in range(3, 20):
        _product(brand=brand, category=category, name=f"Budget Item {number}")
    large = _count(client, url)

    assert large == small


# ---------------------------------------------------------------------------
# Product detail
# ---------------------------------------------------------------------------
def test_product_detail_query_budget(client: Client) -> None:
    _brand, _category, products = _catalogue(products=1, variants=3, images=3)

    count = _count(client, reverse("product_detail", args=[products[0].slug]))

    _assert_budget(count, PRODUCT_DETAIL_QUERY_BUDGET)


def test_product_detail_queries_do_not_grow(client: Client) -> None:
    _brand, category, products = _catalogue(products=1, variants=3, images=3)
    # The wide product exists before the first measurement, so both products
    # have a related product and the same page sections render.
    wide = _product(
        brand=products[0].brand,
        category=category,
        name="Budget Wide",
        variants=20,
        images=20,
    )

    small = _count(client, reverse("product_detail", args=[products[0].slug]))
    large = _count(client, reverse("product_detail", args=[wide.slug]))

    assert large == small


# ---------------------------------------------------------------------------
# Search and the suggestion box
# ---------------------------------------------------------------------------
def test_search_query_budget(client: Client) -> None:
    _catalogue(products=3)
    # The first search of a word writes its log row, so it is warmed first.
    client.get(reverse("product_search"), {"q": "budget"})

    count = _count(client, reverse("product_search"), {"q": "budget"})

    _assert_budget(count, SEARCH_QUERY_BUDGET)


def test_search_queries_do_not_grow(client: Client) -> None:
    _brand, category, products = _catalogue(products=3)
    url = reverse("product_search")
    client.get(url, {"q": "budget"})

    small = _count(client, url, {"q": "budget"})

    for number in range(3, 20):
        _product(brand=products[0].brand, category=category, name=f"Budget Item {number}")
    large = _count(client, url, {"q": "budget"})

    assert large == small


def test_search_suggest_query_budget(client: Client) -> None:
    _catalogue(products=3)

    count = _count(client, reverse("search_suggest"), {"q": "budget"})

    _assert_budget(count, SEARCH_SUGGEST_QUERY_BUDGET)


def test_search_suggest_queries_do_not_grow(client: Client) -> None:
    _brand, category, products = _catalogue(products=3)
    url = reverse("search_suggest")

    small = _count(client, url, {"q": "budget"})

    for number in range(3, 20):
        _product(brand=products[0].brand, category=category, name=f"Budget Item {number}")
    large = _count(client, url, {"q": "budget"})

    assert large == small


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------
def _cart_with(user, products) -> None:
    """One saved cart holding every product's first variant."""
    cart = CartFactory(user=user)
    for product in products:
        CartItemFactory(cart=cart, variant=product.variants.first(), quantity=Decimal("1.000"))


def test_cart_query_budget(client: Client) -> None:
    _brand, _category, products = _catalogue(products=3, variants=1, images=2)
    user = UserFactory()
    _cart_with(user, products)
    client.force_login(user)

    count = _count(client, reverse("cart_detail"))

    _assert_budget(count, CART_QUERY_BUDGET)


def test_cart_queries_do_not_grow(client: Client) -> None:
    _brand, category, products = _catalogue(products=3)
    user = UserFactory()
    _cart_with(user, products)
    client.force_login(user)
    small = _count(client, reverse("cart_detail"))

    cart = Cart.objects.get(user=user)
    for number in range(3, 20):
        product = _product(brand=products[0].brand, category=category, name=f"Budget Item {number}")
        CartItemFactory(cart=cart, variant=product.variants.first(), quantity=Decimal("1.000"))
    large = _count(client, reverse("cart_detail"))

    assert large == small


# ---------------------------------------------------------------------------
# Sitemap
# ---------------------------------------------------------------------------
def test_sitemap_query_budget(client: Client) -> None:
    _catalogue(products=3)

    count = _count(client, reverse("sitemap"))

    _assert_budget(count, SITEMAP_QUERY_BUDGET)


def test_sitemap_queries_do_not_grow(client: Client) -> None:
    _brand, category, _products = _catalogue(products=1)

    small = _count(client, reverse("sitemap"))

    for number in range(20):
        brand = BrandFactory(is_active=True, name=f"Extra Brand {number}")
        _product(brand=brand, category=category, name=f"Extra Item {number}")
    large = _count(client, reverse("sitemap"))

    assert large == small
