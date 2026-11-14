"""Tests for the search-engine tags and the real home page (D87).

REVIEW CAREFULLY: these pages are what a stranger and a search engine see
first, and every address here is public.
"""

from __future__ import annotations

import json
import re
from datetime import time
from decimal import Decimal
from typing import Any

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductImageFactory,
    ProductVariantFactory,
)
from apps.core.models import ShopSettings
from apps.delivery.tests.factories import DeliveryZoneFactory, PinCodeZoneFactory
from apps.inventory.services import receive_stock
from apps.orders.tests.checkout_helpers import open_checkout, post_data, ready_checkout
from apps.orders.tests.factories import OrderFactory, OrderItemFactory
from apps.promotions.models import FeaturedProduct
from apps.promotions.tests.helpers import make_banner, make_coupon
from apps.storefront.presentation import format_cutoff
from apps.storefront.seo import ROBOTS_PRIVATE_PREFIXES

pytestmark = pytest.mark.django_db

# The nine private prefixes robots.txt must keep a search engine out of.
PRIVATE_PREFIXES = [
    "/admin/",
    "/staff/",
    "/cart/",
    "/checkout/",
    "/orders/",
    "/payments/",
    "/account/",
    "/delivery/",
    "/search/",
]


@pytest.fixture(autouse=True)
def _settings_row_and_clean_cache():
    """Create the one settings row and empty its 60-second cache (D58)."""
    cache.clear()
    yield
    cache.clear()


def _product(
    name: str = "Basmati Rice",
    *,
    active: bool = True,
    brand_active: bool = True,
    category_active: bool = True,
    stock: str = "5.000",
) -> Product:
    """One visible product with an active variant unless a rule is overridden."""
    product = ProductFactory(
        name_en=name,
        is_active=active,
        brand=BrandFactory(is_active=brand_active),
        category=CategoryFactory(is_active=category_active),
    )
    variant = ProductVariantFactory(product=product, is_active=True)
    if stock:
        receive_stock(variant=variant, quantity=Decimal(stock))
    return product


def _set_settings(**values: object) -> None:
    """Change the one settings row and save only the fields that changed."""
    row = ShopSettings.load()
    for name, value in values.items():
        setattr(row, name, value)
    row.save(update_fields=[*values, "updated_at"])


def _page(client: Client, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200, path
    return response.content.decode()


def _title(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
    assert match is not None, "The page has no title."
    return match.group(1).strip()


def _canonical(html: str) -> str:
    match = re.search(r'<link rel="canonical" href="([^"]+)">', html)
    assert match is not None, "The page has no canonical link."
    return match.group(1)


def _og(html: str, property_name: str) -> str:
    match = re.search(rf'<meta property="og:{property_name}" content="([^"]*)">', html)
    assert match is not None, f"No og:{property_name} on the page."
    return match.group(1)


def _json_ld(html: str) -> list[dict[str, Any]]:
    """Every JSON-LD block on the page, already parsed."""
    found = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
    # A list comprehension parses them all in one line.
    return [json.loads(block) for block in found]


def _block(html: str, block_type: str) -> dict[str, Any]:
    """The one JSON-LD block of this @type."""
    blocks = [block for block in _json_ld(html) if block.get("@type") == block_type]
    assert len(blocks) == 1, f"Expected one {block_type} block."
    return blocks[0]


def _delivery_text(html: str) -> str:
    match = re.search(r"<p[^>]*data-delivery-text[^>]*>(.*?)</p>", html, re.DOTALL)
    assert match is not None, "The home page has no delivery sentence."
    return match.group(1)


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------
def test_robots_txt_is_plain_text(client: Client) -> None:
    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    assert response.content.decode().splitlines()[0] == "User-agent: *"


def test_robots_disallows_each_private_prefix(client: Client) -> None:
    lines = client.get("/robots.txt").content.decode().splitlines()

    for prefix in PRIVATE_PREFIXES:
        assert f"Disallow: {prefix}" in lines
    assert tuple(PRIVATE_PREFIXES) == ROBOTS_PRIVATE_PREFIXES


def test_robots_names_sitemap_with_site_base_url(settings, client: Client) -> None:
    settings.SITE_BASE_URL = "https://shop.example"

    body = client.get("/robots.txt").content.decode()

    assert "Sitemap: https://shop.example/sitemap.xml" in body


def test_robots_falls_back_to_request_host(settings, client: Client) -> None:
    settings.SITE_BASE_URL = ""

    body = client.get("/robots.txt").content.decode()

    assert "Sitemap: http://testserver/sitemap.xml" in body


# ---------------------------------------------------------------------------
# sitemap.xml
# ---------------------------------------------------------------------------
def test_sitemap_lists_home_categories_brands_products(client: Client) -> None:
    product = _product()

    body = _page(client, "/sitemap.xml")

    assert "<loc>http://testserver/</loc>" in body
    assert f"/category/{product.category.slug}/" in body
    assert f"/brand/{product.brand.slug}/" in body
    assert f"/product/{product.slug}/" in body


def test_sitemap_omits_hidden_product(client: Client) -> None:
    product = _product("Hidden Rice", active=False)

    body = _page(client, "/sitemap.xml")

    assert product.slug not in body


def test_sitemap_omits_inactive_brand(client: Client) -> None:
    brand = BrandFactory(is_active=False)

    body = _page(client, "/sitemap.xml")

    assert f"/brand/{brand.slug}/" not in body


def test_sitemap_omits_inactive_category(client: Client) -> None:
    category = CategoryFactory(is_active=False)
    ProductFactory(category=category, is_active=True)

    body = _page(client, "/sitemap.xml")

    assert f"/category/{category.slug}/" not in body


def test_sitemap_urls_are_absolute(settings, client: Client) -> None:
    _product()
    settings.SITE_BASE_URL = "https://shop.example"

    body = _page(client, "/sitemap.xml")

    locations = re.findall(r"<loc>(.*?)</loc>", body)
    assert locations
    for location in locations:
        assert location.startswith("https://shop.example/")


def test_sitemap_is_valid_xml(client: Client) -> None:
    product = _product()

    body = _page(client, "/sitemap.xml")

    # The project has no XML parser dependency, so the document is checked
    # for the pieces a parser would demand: one declaration, one root and
    # every element closed as often as it is opened.
    assert body.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert body.endswith("</urlset>")
    assert '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' in body
    # Home, the one category, the one brand and the one product.
    assert body.count("<url>") == body.count("</url>") == 4
    assert body.count("<loc>") == body.count("</loc>") == 4
    assert product.slug in body


# ---------------------------------------------------------------------------
# Titles, canonical links and Open Graph tags
# ---------------------------------------------------------------------------
def test_home_has_unique_title(client: Client) -> None:
    html = _page(client, "/")

    assert _title(html) == "Badshah Store — provision store in Indore"


def test_home_has_canonical(client: Client) -> None:
    html = _page(client, "/")

    assert _canonical(html) == "http://testserver/"


def test_home_has_open_graph_title(client: Client) -> None:
    html = _page(client, "/")

    assert _og(html, "title") == "Badshah Store"
    assert _og(html, "url") == "http://testserver/"


def test_list_has_unique_title(client: Client) -> None:
    html = _page(client, "/products/")

    assert _title(html) == "All products — Badshah Store"


def test_list_has_canonical(client: Client) -> None:
    html = _page(client, "/products/")

    assert _canonical(html) == "http://testserver/products/"


def test_list_has_open_graph_title(client: Client) -> None:
    html = _page(client, "/products/")

    assert _og(html, "title") == "All products"


def test_category_has_unique_title(client: Client) -> None:
    category = CategoryFactory(name="Snacks", is_active=True)

    html = _page(client, f"/category/{category.slug}/")

    assert _title(html) == "Snacks — Badshah Store"


def test_category_has_canonical(client: Client) -> None:
    category = CategoryFactory(name="Snacks", is_active=True)

    html = _page(client, f"/category/{category.slug}/")

    assert _canonical(html) == f"http://testserver/category/{category.slug}/"


def test_category_has_open_graph_title(client: Client) -> None:
    category = CategoryFactory(name="Snacks", is_active=True)

    html = _page(client, f"/category/{category.slug}/")

    assert _og(html, "title") == "Snacks"


def test_brand_has_unique_title(client: Client) -> None:
    brand = BrandFactory(name="Nestle", is_active=True)

    html = _page(client, f"/brand/{brand.slug}/")

    assert _title(html) == "Nestle — Badshah Store"


def test_brand_has_canonical(client: Client) -> None:
    brand = BrandFactory(name="Nestle", is_active=True)

    html = _page(client, f"/brand/{brand.slug}/")

    assert _canonical(html) == f"http://testserver/brand/{brand.slug}/"


def test_brand_has_open_graph_title(client: Client) -> None:
    brand = BrandFactory(name="Nestle", is_active=True)

    html = _page(client, f"/brand/{brand.slug}/")

    assert _og(html, "title") == "Nestle"


def test_product_detail_has_unique_title(client: Client) -> None:
    product = _product("Basmati Rice")

    html = _page(client, f"/product/{product.slug}/")

    assert _title(html) == f"Basmati Rice — {product.brand.name} — Badshah Store"


def test_product_detail_has_canonical(client: Client) -> None:
    product = _product()

    html = _page(client, f"/product/{product.slug}/")

    assert _canonical(html) == f"http://testserver/product/{product.slug}/"


def test_product_detail_has_open_graph_title(client: Client) -> None:
    product = _product("Basmati Rice")

    html = _page(client, f"/product/{product.slug}/")

    assert _og(html, "title") == f"Basmati Rice — {product.brand.name}"


def test_filtered_listing_is_noindex_with_clean_canonical(client: Client) -> None:
    _product()

    html = _page(client, "/products/?sort=price_asc&vegan=1")

    assert '<meta name="robots" content="noindex, follow">' in html
    assert _canonical(html) == "http://testserver/products/"


def test_page_two_canonical_keeps_page_parameter(client: Client) -> None:
    _product()

    html = _page(client, "/products/?page=2")

    assert _canonical(html) == "http://testserver/products/?page=2"
    assert '<meta name="robots"' not in html


def test_og_image_present_when_product_has_image(client: Client) -> None:
    product = _product()
    ProductImageFactory(product=product)

    html = _page(client, f"/product/{product.slug}/")

    image = _og(html, "image")
    assert image.startswith("http://testserver/media/")


def test_og_image_absent_when_no_image(client: Client) -> None:
    product = _product()

    html = _page(client, f"/product/{product.slug}/")

    assert 'property="og:image"' not in html


def test_meta_description_is_cut_to_160_characters(client: Client) -> None:
    product = _product("Basmati Rice")
    product.description = "Fresh long grain rice from the fields. " * 10
    product.save(update_fields=["description"])

    html = _page(client, f"/product/{product.slug}/")

    match = re.search(r'<meta name="description" content="([^"]*)">', html)
    assert match is not None, "The product page has no meta description."
    assert len(match.group(1)) <= 160


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------
def test_breadcrumb_json_ld_on_category(client: Client) -> None:
    category = CategoryFactory(name="Snacks", is_active=True)

    html = _page(client, f"/category/{category.slug}/")

    block = _block(html, "BreadcrumbList")
    items = block["itemListElement"]
    assert items[0]["position"] == 1
    assert items[0]["name"] == "Home"
    assert items[0]["item"] == "http://testserver/"
    assert items[1]["name"] == "Snacks"


def test_breadcrumb_json_ld_on_product(client: Client) -> None:
    product = _product("Basmati Rice")

    html = _page(client, f"/product/{product.slug}/")

    items = _block(html, "BreadcrumbList")["itemListElement"]
    # Home, then the product's category, then the product itself.
    assert [item["position"] for item in items] == [1, 2, 3]
    assert items[-1]["name"] == "Basmati Rice"
    assert "item" not in items[-1]


def test_home_has_organization_json_ld(client: Client) -> None:
    _set_settings(
        shop_name="Badshah Store",
        address="12 Saifee Nagar, Indore",
        phone="+919876543210",
        email="shop@example.com",
    )

    block = _block(_page(client, "/"), "GroceryStore")

    assert block["name"] == "Badshah Store"
    assert block["address"]["streetAddress"] == "12 Saifee Nagar, Indore"
    assert block["telephone"] == "+919876543210"
    assert block["email"] == "shop@example.com"
    assert block["url"] == "http://testserver/"


def test_home_has_website_search_action(client: Client) -> None:
    block = _block(_page(client, "/"), "WebSite")

    assert block["potentialAction"]["@type"] == "SearchAction"
    assert block["potentialAction"]["target"]["urlTemplate"].endswith(
        "/search/?q={search_term_string}"
    )


def test_json_ld_skips_blank_shop_fields(client: Client) -> None:
    _set_settings(shop_name="Badshah Store", address="", phone="", email="")

    block = _block(_page(client, "/"), "GroceryStore")

    assert block["name"] == "Badshah Store"
    assert "address" not in block
    assert "telephone" not in block
    assert "email" not in block


def test_json_ld_cannot_be_broken_by_script_close_in_name(client: Client) -> None:
    product = _product("</script><b>Chai")

    html = _page(client, f"/product/{product.slug}/")

    assert "</script><b>" not in html
    assert "\\u003c/script\\u003e" in html
    # The blocks still parse, so no name ended one early.
    assert len(_json_ld(html)) >= 2


def test_json_ld_is_valid_json(client: Client) -> None:
    product = _product('Chai "Special" </script>')

    home_blocks = _json_ld(_page(client, "/"))
    product_blocks = _json_ld(_page(client, f"/product/{product.slug}/"))

    assert {block["@type"] for block in home_blocks} == {"GroceryStore", "WebSite"}
    assert {block["@type"] for block in product_blocks} == {"Product", "BreadcrumbList"}


# ---------------------------------------------------------------------------
# The home page copy
# ---------------------------------------------------------------------------
def test_home_no_longer_says_coming_soon(client: Client) -> None:
    html = _page(client, "/")

    assert "Online store coming soon" not in html
    assert "now taking orders online" in html


def test_home_has_no_alpine_demo(client: Client) -> None:
    html = _page(client, "/")

    assert "Toggle Alpine demo" not in html
    assert "Alpine is working" not in html
    assert "Developer check" not in html


def test_home_h1_is_shop_name(client: Client) -> None:
    _set_settings(shop_name="Sharma Kirana")

    html = _page(client, "/")

    assert ">Sharma Kirana</h1>" in html


def test_home_h1_falls_back_when_name_blank(client: Client) -> None:
    _set_settings(shop_name="")

    html = _page(client, "/")

    assert ">Badshah Store</h1>" in html


def test_home_delivery_text_uses_configured_pins(client: Client) -> None:
    zone = DeliveryZoneFactory()
    for pincode in ("453331", "452001", "452002"):
        PinCodeZoneFactory(zone=zone, pincode=pincode)
    # A pin in a switched-off zone is never promised.
    closed_zone = DeliveryZoneFactory(is_active=False)
    PinCodeZoneFactory(zone=closed_zone, pincode="452014")
    _set_settings(same_day_cutoff=time(16, 0))

    text = _delivery_text(_page(client, "/"))

    assert "before 4 pm" in text
    assert "452001, 452002, 453331" in text
    assert "452014" not in text
    assert "5 pm" not in text


def test_home_links_to_pin_check(client: Client) -> None:
    html = _page(client, "/")

    assert 'href="/delivery/check/"' in html
    assert "Check your pin code" in html


def test_home_still_shows_banners_and_featured(client: Client) -> None:
    banner = make_banner(alt_text="Diwali offer banner")
    product = _product("Basmati Rice")
    FeaturedProduct.objects.create(product=product, sort_order=1)

    html = _page(client, "/")

    assert 'alt="Diwali offer banner"' in html
    assert "Basmati Rice" in html
    assert banner.alt_text in html


# ---------------------------------------------------------------------------
# The checkout coupon buttons
# ---------------------------------------------------------------------------
def test_remove_button_absent_without_coupon() -> None:
    _user, _address_row, _row, client = ready_checkout()

    html = client.get(reverse("checkout")).content.decode()

    assert 'name="coupon_action" value="apply"' in html
    assert 'name="coupon_action" value="remove"' not in html
    assert "data-coupon-remove" not in html


def test_remove_button_present_with_coupon() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)
    data = post_data(group, token, address_row)
    data.update({"coupon_code": "DIWALI10", "coupon_action": "apply"})

    html = client.post(reverse("checkout"), data).content.decode()

    assert 'name="coupon_action" value="remove"' in html
    assert "data-coupon-remove" in html


# ---------------------------------------------------------------------------
# Pages that were noindex before this slice
# ---------------------------------------------------------------------------
def test_private_pages_still_noindex() -> None:
    user, _address_row, _row, client = ready_checkout()
    order = OrderFactory(user=user)
    OrderItemFactory(order=order)

    checkout = client.get(reverse("checkout")).content.decode()
    orders = client.get(reverse("order_list")).content.decode()
    detail = client.get(reverse("order_detail", args=[order.checkout_group])).content.decode()

    # Every one of the three must be a real page, not a redirect's empty body.
    assert checkout and orders and detail
    for html in (checkout, orders, detail):
        assert '<meta name="robots" content="noindex">' in html


def test_format_cutoff_reads_the_hour_like_a_customer() -> None:
    assert format_cutoff(time(17, 0)) == "5 pm"
    assert format_cutoff(time(9, 30)) == "9:30 am"
    assert format_cutoff(time(0, 0)) == "12 am"
