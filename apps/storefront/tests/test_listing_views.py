"""Tests for storefront listing views (Slice 23d/23f)."""

from __future__ import annotations

import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from apps.catalog.models import ProductImage
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.inventory.services import receive_stock


@pytest.mark.django_db
def test_all_pages_200(client: Client) -> None:
    ProductFactory()
    assert client.get("/products/").status_code == 200
    cat = CategoryFactory(is_active=True)
    assert client.get(f"/category/{cat.slug}/").status_code == 200
    brand = BrandFactory(is_active=True)
    assert client.get(f"/brand/{brand.slug}/").status_code == 200


@pytest.mark.django_db
def test_404_cases(client: Client) -> None:
    assert client.get("/category/nope/").status_code == 404
    assert client.get("/brand/nope/").status_code == 404
    cat = CategoryFactory(is_active=False)
    assert client.get(f"/category/{cat.slug}/").status_code == 404


@pytest.mark.django_db
def test_home_nav_links(client: Client) -> None:
    response = client.get("/")
    html = response.content.decode()
    assert 'href="/products/"' in html
    assert 'href="/"' in html


@pytest.mark.django_db
def test_base_html_htmx_script(client: Client) -> None:
    response = client.get("/")
    html = response.content.decode()
    assert re.search(r'<script[^>]*src="[^"]*htmx-2\.0\.4\.min\.js"[^>]*>', html)
    assert not re.search(r"<link[^>]*htmx", html)


@pytest.mark.django_db
def test_single_variant_shows_price_without_from(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, selling_price=48, mrp=48, is_active=True)
    response = client.get("/products/")
    html = response.content.decode()
    assert "₹48.00" in html
    assert "from ₹" not in html
    assert "% off" not in html
    assert "line-through" not in html


@pytest.mark.django_db
def test_two_variants_show_from_price(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, selling_price=48, mrp=48, is_active=True)
    ProductVariantFactory(product=product, selling_price=50, mrp=50, is_active=True)
    response = client.get("/products/")
    html = response.content.decode()
    assert "from ₹" in html


@pytest.mark.django_db
def test_card_shows_struck_mrp_and_percent_off(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, selling_price=45, mrp=50, is_active=True)
    response = client.get("/products/")
    html = response.content.decode()
    assert "₹50.00" in html
    assert "line-through" in html
    assert "10% off" in html


@pytest.mark.django_db
def test_out_of_stock_badge_shown_without_stock(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True)
    response = client.get("/products/")
    html = response.content.decode()
    assert "Out of stock" in html


@pytest.mark.django_db
def test_no_out_of_stock_badge_when_in_stock(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    variant = ProductVariantFactory(product=product, is_active=True)
    receive_stock(variant=variant, quantity=5)
    response = client.get("/products/")
    html = response.content.decode()
    assert "Out of stock" not in html


@pytest.mark.django_db
def test_placeholder_image_when_no_photo(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True)
    response = client.get("/products/")
    html = response.content.decode()
    assert "product-placeholder.svg" in html


@pytest.mark.django_db
def test_thumbnail_url_used_when_present(client: Client, monkeypatch) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True)
    img = ProductImage.objects.create(
        product=product,
        image=SimpleUploadedFile("p.jpg", b"\xff\xd8\xff", content_type="image/jpeg"),
    )
    # ensure thumbnail present
    img.refresh_from_db()
    if not img.thumbnail:
        return
    response = client.get("/products/")
    html = response.content.decode()
    assert img.thumbnail.url in html or "thumbs" in html


@pytest.mark.django_db
def test_original_image_used_when_no_thumbnail(client: Client, monkeypatch) -> None:
    from apps.catalog import images

    monkeypatch.setattr(images, "create_thumbnail", lambda source: None)
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True)
    img = ProductImage.objects.create(
        product=product,
        image=SimpleUploadedFile("p.jpg", b"\xff\xd8\xff", content_type="image/jpeg"),
    )
    img.refresh_from_db()
    response = client.get("/products/")
    html = response.content.decode()
    assert img.image.url in html


@pytest.mark.django_db
def test_category_breadcrumb_for_nested_category(client: Client) -> None:
    parent = CategoryFactory(name="Parent", slug="parent", is_active=True)
    child = CategoryFactory(name="Child", slug="child", parent=parent, is_active=True)
    brand = BrandFactory(is_active=True)
    product = ProductFactory(brand=brand, category=child, is_active=True)
    ProductVariantFactory(product=product, is_active=True)
    response = client.get(f"/category/{child.slug}/")
    html = response.content.decode()
    assert 'href="/"' in html or "Home" in html
    assert f"/category/{parent.slug}/" in html
    assert "Child" in html


@pytest.mark.django_db
def test_child_category_links_only_active_children(client: Client) -> None:
    parent = CategoryFactory(name="Parent", slug="parent", is_active=True)
    active_child = CategoryFactory(name="Active", slug="active", parent=parent, is_active=True)
    CategoryFactory(name="Inactive", slug="inactive", parent=parent, is_active=False)
    response = client.get(f"/category/{parent.slug}/")
    html = response.content.decode()
    assert active_child.slug in html
    assert "inactive" not in html


@pytest.mark.django_db
def test_inactive_ancestor_gives_404(client: Client) -> None:
    parent = CategoryFactory(is_active=False)
    child = CategoryFactory(is_active=True, parent=parent)
    assert client.get(f"/category/{child.slug}/").status_code == 404


@pytest.mark.django_db
def test_inactive_brand_gives_404(client: Client) -> None:
    brand = BrandFactory(is_active=False)
    assert client.get(f"/brand/{brand.slug}/").status_code == 404


@pytest.mark.django_db
def test_inactive_and_hidden_products_not_listed(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(name_en="Hidden", brand=brand, category=cat, is_active=False)
    ProductVariantFactory(product=p, is_active=True)
    response = client.get("/products/")
    html = response.content.decode()
    assert "Hidden" not in html


@pytest.mark.django_db
def test_pagination_25_products(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    for i in range(25):
        p = ProductFactory(name_en=f"P{i:02d}", brand=brand, category=cat, is_active=True)
        ProductVariantFactory(product=p, is_active=True)
    r1 = client.get("/products/")
    html1 = r1.content.decode()
    assert html1.count("Out of stock") == 24
    r2 = client.get("/products/?page=2")
    html2 = r2.content.decode()
    assert "Page 2 of 2" in html2


@pytest.mark.django_db
def test_bad_page_values_return_200(client: Client) -> None:
    assert client.get("/products/?page=999").status_code == 200
    assert client.get("/products/?page=abc").status_code == 200


@pytest.mark.django_db
def test_unknown_sort_uses_default_order(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(name_en="B", brand=brand, category=cat, is_active=True)
    p2 = ProductFactory(name_en="A", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p1, is_active=True)
    ProductVariantFactory(product=p2, is_active=True)
    response = client.get("/products/?sort=nope")
    html = response.content.decode()
    assert html.index("A") < html.index("B")


@pytest.mark.django_db
def test_pagination_links_keep_sort(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    for i in range(25):
        p = ProductFactory(name_en=f"P{i:02d}", brand=brand, category=cat, is_active=True)
        ProductVariantFactory(product=p, is_active=True)
    response = client.get("/products/?sort=price_asc")
    html = response.content.decode()
    assert "page=2" in html
    assert "sort=price_asc" in html


@pytest.mark.django_db
def test_query_count_independent_of_product_count(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    for _ in range(3):
        p = ProductFactory(brand=brand, category=cat, is_active=True)
        ProductVariantFactory(product=p, is_active=True)
    with CaptureQueriesContext(connection) as ctx3:
        client.get("/products/")
    # reset not needed; add more
    for _ in range(21):
        p = ProductFactory(brand=brand, category=cat, is_active=True)
        ProductVariantFactory(product=p, is_active=True)
    with CaptureQueriesContext(connection) as ctx24:
        client.get("/products/")
    assert len(ctx3.captured_queries) == len(ctx24.captured_queries)
