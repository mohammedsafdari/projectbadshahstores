"""Tests for the banners and featured products on the home page (D84).

REVIEW CAREFULLY: this is the public front page, and the order staff chose.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.inventory.services import receive_stock
from apps.promotions.models import Banner, FeaturedProduct
from apps.promotions.tests.helpers import make_banner
from apps.storefront.presentation import home_text
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _product(
    name: str,
    *,
    active: bool = True,
    brand_active: bool = True,
    category_active: bool = True,
    has_variant: bool = True,
    in_stock: bool = True,
) -> Product:
    """One visible product with one live variant, unless a rule is overridden."""
    product = ProductFactory(
        name_en=name,
        is_active=active,
        brand=BrandFactory(is_active=brand_active),
        category=CategoryFactory(is_active=category_active),
    )
    if has_variant:
        variant = ProductVariantFactory(product=product, is_active=True)
        if in_stock:
            receive_stock(variant=variant, quantity=Decimal("5"))
    return product


def _feature(*products: Product) -> None:
    """Put the products on the home page in the order given."""
    for index, product in enumerate(products, start=1):
        FeaturedProduct.objects.create(product=product, sort_order=index)


def _home(client: Client) -> str:
    response = client.get("/")
    assert response.status_code == 200
    return response.content.decode()


# ---------------------------------------------------------------------------
# Banners
# ---------------------------------------------------------------------------
def test_a_visible_banner_shows_its_alt_text(client: Client) -> None:
    make_banner(alt_text="Diwali offer banner")

    html = _home(client)

    assert 'alt="Diwali offer banner"' in html


def test_a_visible_banner_shows_its_size_attributes(client: Client) -> None:
    make_banner()

    html = _home(client)

    # make_banner uploads an 800x400 image, and Django stores its size.
    assert 'width="800" height="400"' in html


def test_the_display_copy_is_used_when_it_exists(client: Client) -> None:
    banner = make_banner()

    html = _home(client)

    assert banner.display.url in html


def test_the_original_image_is_used_when_there_is_no_display_copy(client: Client) -> None:
    banner = make_banner()
    Banner.objects.filter(pk=banner.pk).update(display="")

    html = _home(client)

    assert banner.image.url in html
    assert "/banners/display/" not in html


def test_a_linked_banner_is_wrapped_in_one_link(client: Client) -> None:
    make_banner(link_url="/offers")

    html = _home(client)

    assert re.search(r'<a [^>]*href="/offers"[^>]*>\s*<img', html)


def test_an_https_link_gets_rel_noopener_and_noreferrer(client: Client) -> None:
    make_banner(link_url="https://example.com/diwali")

    html = _home(client)

    assert 'rel="noopener noreferrer"' in html


def test_an_internal_link_has_no_rel_attribute(client: Client) -> None:
    make_banner(link_url="/offers")

    html = _home(client)

    assert 'rel="noopener noreferrer"' not in html


def test_the_first_banner_is_eager_and_the_rest_are_lazy(client: Client) -> None:
    make_banner(title="First", sort_order=1)
    make_banner(title="Second", sort_order=2)

    html = _home(client)

    assert html.count('loading="eager"') == 1
    assert html.count('loading="lazy"') == 1


def test_an_expired_banner_is_absent(client: Client) -> None:
    make_banner(alt_text="Expired banner", ends_at=timezone.now() - timedelta(hours=1))

    html = _home(client)

    assert "Expired banner" not in html


def test_an_inactive_banner_is_absent(client: Client) -> None:
    make_banner(alt_text="Switched off banner", is_active=False)

    html = _home(client)

    assert "Switched off banner" not in html


# ---------------------------------------------------------------------------
# The sections
# ---------------------------------------------------------------------------
def test_no_banner_and_no_featured_product_prints_no_section(client: Client) -> None:
    html = _home(client)

    assert "Featured products" not in html
    assert "Offers" not in html


def test_featured_cards_are_in_the_stored_order(client: Client) -> None:
    basmati = _product("Basmati Rice")
    dal = _product("Toor Dal")
    # Dal was created later, so its id is higher; the staff order still wins.
    _feature(dal, basmati)

    html = _home(client)

    assert html.index("Toor Dal") < html.index("Basmati Rice")


def test_an_inactive_product_is_absent_while_the_others_stay(client: Client) -> None:
    _feature(_product("Hidden Rice", active=False), _product("Shown Dal"))

    html = _home(client)

    assert "Hidden Rice" not in html
    assert "Shown Dal" in html


def test_a_product_of_an_inactive_brand_is_absent(client: Client) -> None:
    _feature(_product("Brand Gone Atta", brand_active=False), _product("Shown Dal"))

    html = _home(client)

    assert "Brand Gone Atta" not in html
    assert "Shown Dal" in html


def test_a_product_of_a_hidden_category_is_absent(client: Client) -> None:
    _feature(_product("Category Gone Atta", category_active=False), _product("Shown Dal"))

    html = _home(client)

    assert "Category Gone Atta" not in html
    assert "Shown Dal" in html


def test_a_product_with_no_active_variant_is_absent(client: Client) -> None:
    _feature(_product("No Variant Atta", has_variant=False), _product("Shown Dal"))

    html = _home(client)

    assert "No Variant Atta" not in html
    assert "Shown Dal" in html


def test_an_out_of_stock_product_still_shows_its_card(client: Client) -> None:
    _feature(_product("Sold Out Atta", in_stock=False))

    html = _home(client)

    assert "Sold Out Atta" in html
    assert "Out of stock" in html


def test_the_existing_home_content_is_unchanged(client: Client, settings) -> None:
    # The debug-only section is a context processor: it needs DEBUG and the test
    # client's address listed as internal, exactly as a developer's browser is.
    settings.DEBUG = True
    settings.INTERNAL_IPS = ["127.0.0.1"]
    make_banner()
    _feature(_product("Basmati Rice"))

    html = _home(client)

    assert "Badshah Store" in html
    assert "Online store coming soon" in html
    assert "Developer check" in html


# ---------------------------------------------------------------------------
# Queries and visitors
# ---------------------------------------------------------------------------
def test_the_query_count_does_not_grow_with_the_number_of_featured_products(
    client: Client, django_assert_max_num_queries
) -> None:
    make_banner()
    products = [_product(f"Product {index}") for index in range(12)]
    _feature(*products[:3])

    # The number is the same whether three or twelve cards are printed.
    with django_assert_max_num_queries(12):
        assert client.get("/").status_code == 200

    _feature(*products[3:])

    with django_assert_max_num_queries(12):
        assert client.get("/").status_code == 200


def test_the_page_works_for_a_signed_out_visitor(client: Client) -> None:
    make_banner()
    _feature(_product("Basmati Rice"))

    assert client.get("/").status_code == 200


def test_the_page_works_for_a_signed_in_customer(client: Client) -> None:
    client.force_login(UserFactory())
    make_banner()
    _feature(_product("Basmati Rice"))

    assert client.get("/").status_code == 200


# ---------------------------------------------------------------------------
# The words
# ---------------------------------------------------------------------------
def test_the_featured_heading_words() -> None:
    assert home_text()["featured_heading"] == "Featured products"


def test_the_offers_heading_words() -> None:
    assert home_text()["offers_heading"] == "Offers"
