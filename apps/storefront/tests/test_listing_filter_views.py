"""Tests for storefront listing filter views (Slice 24b)."""

from __future__ import annotations

import re

import pytest
from django.test import Client

from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.inventory.services import receive_stock


@pytest.mark.django_db
def test_full_page_has_filter_form_wired_for_htmx(client: Client) -> None:
    response = client.get("/products/")
    html = response.content.decode()
    assert 'id="listing-form"' in html
    assert "hx-get=" in html
    assert 'hx-target="#listing-results"' in html
    assert 'hx-push-url="true"' in html
    assert 'id="listing-results"' in html


@pytest.mark.django_db
def test_brand_checkboxes_listed_and_selected_ones_checked(client: Client) -> None:
    b1 = BrandFactory(is_active=True, name="Alpha", slug="alpha")
    b2 = BrandFactory(is_active=True, name="Beta", slug="beta")
    cat = CategoryFactory(is_active=True)
    for b in (b1, b2):
        p = ProductFactory(brand=b, category=cat, is_active=True)
        ProductVariantFactory(product=p, is_active=True)
    response = client.get("/products/?brand=alpha")
    html = response.content.decode()
    assert 'value="alpha"' in html
    assert "checked" in html


@pytest.mark.django_db
def test_brand_filter_limits_products_page(client: Client) -> None:
    b1 = BrandFactory(is_active=True, name="Alpha", slug="alpha")
    b2 = BrandFactory(is_active=True, name="Beta", slug="beta")
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(name_en="P1", brand=b1, category=cat, is_active=True)
    p2 = ProductFactory(name_en="P2", brand=b2, category=cat, is_active=True)
    ProductVariantFactory(product=p1, is_active=True)
    ProductVariantFactory(product=p2, is_active=True)
    response = client.get("/products/?brand=alpha")
    html = response.content.decode()
    assert "P1" in html
    assert "P2" not in html


@pytest.mark.django_db
def test_two_brands_are_or_on_products_page(client: Client) -> None:
    b1 = BrandFactory(is_active=True, name="Alpha", slug="alpha")
    b2 = BrandFactory(is_active=True, name="Beta", slug="beta")
    b3 = BrandFactory(is_active=True, name="Gamma", slug="gamma")
    cat = CategoryFactory(is_active=True)
    for b, name in ((b1, "P1"), (b2, "P2"), (b3, "P3")):
        p = ProductFactory(name_en=name, brand=b, category=cat, is_active=True)
        ProductVariantFactory(product=p, is_active=True)
    response = client.get("/products/?brand=alpha&brand=beta")
    html = response.content.decode()
    assert "P1" in html
    assert "P2" in html
    assert "P3" not in html


@pytest.mark.django_db
def test_price_filter_limits_results(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(name_en="Cheap", brand=brand, category=cat, is_active=True)
    p2 = ProductFactory(name_en="Expensive", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p1, is_active=True, selling_price=10, mrp=10)
    ProductVariantFactory(product=p2, is_active=True, selling_price=100, mrp=100)
    response = client.get("/products/?min_price=50&max_price=150")
    html = response.content.decode()
    assert "Expensive" in html
    assert "Cheap" not in html


@pytest.mark.django_db
def test_own_brand_filter_limits_results(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(name_en="Own", brand=brand, category=cat, is_active=True, is_own_brand=True)
    p2 = ProductFactory(
        name_en="Other", brand=brand, category=cat, is_active=True, is_own_brand=False
    )
    ProductVariantFactory(product=p1, is_active=True)
    ProductVariantFactory(product=p2, is_active=True)
    response = client.get("/products/?own_brand=1")
    html = response.content.decode()
    assert "Own" in html
    assert "Other" not in html


@pytest.mark.django_db
def test_in_stock_filter_limits_results(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(name_en="Stocked", brand=brand, category=cat, is_active=True)
    p2 = ProductFactory(name_en="Empty", brand=brand, category=cat, is_active=True)
    v1 = ProductVariantFactory(product=p1, is_active=True)
    ProductVariantFactory(product=p2, is_active=True)
    receive_stock(variant=v1, quantity=5)
    response = client.get("/products/?in_stock=1")
    html = response.content.decode()
    assert "Stocked" in html
    assert "Empty" not in html


@pytest.mark.django_db
def test_veg_filter_limits_results(client: Client) -> None:
    from apps.catalog.models import Product

    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(
        name_en="Veg", brand=brand, category=cat, is_active=True, diet_type=Product.DietType.VEG
    )
    p2 = ProductFactory(
        name_en="NonVeg",
        brand=brand,
        category=cat,
        is_active=True,
        diet_type=Product.DietType.NON_VEG,
    )
    ProductVariantFactory(product=p1, is_active=True)
    ProductVariantFactory(product=p2, is_active=True)
    response = client.get("/products/?veg=1")
    html = response.content.decode()
    assert "Veg" in html
    assert "NonVeg" not in html


@pytest.mark.django_db
def test_bad_filter_input_is_ignored_and_page_still_loads(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(name_en="Prod", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p, is_active=True)
    response = client.get("/products/?min_price=abc&max_price=-5&brand=nope")
    assert response.status_code == 200
    html = response.content.decode()
    assert "Prod" in html


@pytest.mark.django_db
def test_pagination_links_keep_filters(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    for i in range(25):
        p = ProductFactory(
            name_en=f"P{i:02d}", brand=brand, category=cat, is_active=True, is_own_brand=True
        )
        ProductVariantFactory(product=p, is_active=True)
    response = client.get("/products/?own_brand=1")
    html = response.content.decode()
    import re

    m = re.search(r"<a[^>]*>Next</a>", html)
    assert m is not None
    assert "page=2" in m.group(0)
    assert "own_brand=1" in m.group(0)


@pytest.mark.django_db
def test_pagination_links_have_htmx_attributes(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    for i in range(25):
        p = ProductFactory(
            name_en=f"P{i:02d}", brand=brand, category=cat, is_active=True, is_own_brand=True
        )
        ProductVariantFactory(product=p, is_active=True)
    response = client.get("/products/?own_brand=1")
    html = response.content.decode()
    import re

    m = re.search(r"<a[^>]*>Next</a>", html)
    assert m is not None
    assert "hx-get" in m.group(0)
    assert 'hx-target="#listing-results"' in m.group(0)
    assert 'hx-push-url="true"' in m.group(0)


@pytest.mark.django_db
def test_htmx_request_returns_only_results_block(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(name_en="Prod", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p, is_active=True)
    response = client.get("/products/", HTTP_HX_REQUEST="true")
    html = response.content.decode()
    assert response.status_code == 200
    assert "Prod" in html
    assert "<html" not in html
    assert 'id="listing-form"' not in html


@pytest.mark.django_db
def test_plain_request_returns_full_page(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(name_en="Prod", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p, is_active=True)
    response = client.get("/products/")
    html = response.content.decode()
    assert "<html" in html
    assert 'id="listing-form"' in html
    assert 'id="listing-results"' in html


@pytest.mark.django_db
def test_htmx_history_restore_returns_full_page(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(name_en="Prod", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p, is_active=True)
    response = client.get(
        "/products/", HTTP_HX_REQUEST="true", HTTP_HX_HISTORY_RESTORE_REQUEST="true"
    )
    html = response.content.decode()
    assert "<html" in html


@pytest.mark.django_db
def test_response_has_vary_hx_request(client: Client) -> None:
    r1 = client.get("/products/")
    r2 = client.get("/products/", HTTP_HX_REQUEST="true")
    assert "HX-Request" in r1["Vary"]
    assert "HX-Request" in r2["Vary"]


@pytest.mark.django_db
def test_result_count_text(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(name_en="Prod", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p, is_active=True)
    response = client.get("/products/")
    html = response.content.decode()
    m = re.search(r">(\d+ products?)</p>", html)
    assert m is not None
    assert m.group(1) == "1 product"
    # 3 products
    for i in range(2):
        p2 = ProductFactory(name_en=f"P{i}", brand=brand, category=cat, is_active=True)
        ProductVariantFactory(product=p2, is_active=True)
    response = client.get("/products/")
    html = response.content.decode()
    m = re.search(r">(\d+ products?)</p>", html)
    assert m is not None
    assert m.group(1) == "3 products"


@pytest.mark.django_db
def test_clear_filters_link_only_when_filters_active(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(name_en="Prod", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p, is_active=True)
    r1 = client.get("/products/")
    assert "Clear filters" not in r1.content.decode()
    r2 = client.get("/products/?own_brand=1")
    assert "Clear filters" in r2.content.decode()


@pytest.mark.django_db
def test_empty_result_message_with_filters(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(name_en="Prod", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p, is_active=True)
    response = client.get("/products/?min_price=1000000")
    html = response.content.decode()
    assert "No products match these filters." in html
    assert "Clear filters" in html


@pytest.mark.django_db
def test_clear_link_keeps_non_default_sort(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(name_en="Prod", brand=brand, category=cat, is_active=True, is_own_brand=True)
    ProductVariantFactory(product=p, is_active=True)
    response = client.get("/products/?own_brand=1&sort=price_asc")
    html = response.content.decode()
    assert "/products/?sort=price_asc" in html
    response2 = client.get("/products/?own_brand=1")
    html2 = response2.content.decode()
    assert 'href="/products/"' in html2


@pytest.mark.django_db
def test_brand_page_ignores_brand_param_and_hides_brand_filter(client: Client) -> None:
    b1 = BrandFactory(is_active=True, name="A", slug="a")
    b2 = BrandFactory(is_active=True, name="B", slug="b")
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(name_en="Prod", brand=b1, category=cat, is_active=True)
    ProductVariantFactory(product=p, is_active=True)
    response = client.get(f"/brand/{b1.slug}/?brand={b2.slug}")
    html = response.content.decode()
    assert "Prod" in html
    assert 'name="brand"' not in html


@pytest.mark.django_db
def test_category_page_offers_only_its_brands(client: Client) -> None:
    b1 = BrandFactory(is_active=True, name="A", slug="a")
    b2 = BrandFactory(is_active=True, name="B", slug="b")
    parent = CategoryFactory(is_active=True, name="Parent", slug="parent")
    child = CategoryFactory(is_active=True, name="Child", slug="child", parent=parent)
    other = CategoryFactory(is_active=True, name="Other", slug="other")
    p1 = ProductFactory(name_en="P1", brand=b1, category=child, is_active=True)
    p2 = ProductFactory(name_en="P2", brand=b2, category=other, is_active=True)
    ProductVariantFactory(product=p1, is_active=True)
    ProductVariantFactory(product=p2, is_active=True)
    response = client.get(f"/category/{child.slug}/")
    html = response.content.decode()
    assert 'value="a"' in html
    assert 'value="b"' not in html


@pytest.mark.django_db
def test_filter_panel_open_only_when_filters_active(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(name_en="Prod", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p, is_active=True)
    r1 = client.get("/products/")
    part1 = r1.content.decode().split("<details")[1].split(">")[0]
    assert "<details" in r1.content.decode()
    assert " open" not in part1
    r2 = client.get("/products/?own_brand=1")
    part2 = r2.content.decode().split("<details")[1].split(">")[0]
    assert "<details" in r2.content.decode()
    assert " open" in part2


@pytest.mark.django_db
def test_filters_and_sort_combine(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(
        name_en="Cheap", brand=brand, category=cat, is_active=True, is_own_brand=True
    )
    p2 = ProductFactory(
        name_en="Expensive", brand=brand, category=cat, is_active=True, is_own_brand=True
    )
    ProductVariantFactory(product=p1, is_active=True, selling_price=10, mrp=10)
    ProductVariantFactory(product=p2, is_active=True, selling_price=100, mrp=100)
    response = client.get("/products/?own_brand=1&sort=price_desc")
    html = response.content.decode()
    # names present
    assert "Cheap" in html
    assert "Expensive" in html


@pytest.mark.django_db
def test_result_count_is_total_not_page_size(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    for i in range(25):
        p = ProductFactory(name_en=f"P{i:02d}", brand=brand, category=cat, is_active=True)
        ProductVariantFactory(product=p, is_active=True)
    for url in ("/products/", "/products/?page=2"):
        response = client.get(url)
        html = response.content.decode()
        m = re.search(r">(\d+ products?)</p>", html)
        assert m is not None
        assert m.group(1) == "25 products"


@pytest.mark.django_db
def test_veg_checkbox_checked_from_the_address(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(name_en="Prod", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p, is_active=True)
    response = client.get("/products/?veg=1")
    html = response.content.decode()
    m = re.search(r'<input[^>]*name="veg"[^>]*>', html)
    assert m is not None
    assert "checked" in m.group(0)
    response2 = client.get("/products/")
    html2 = response2.content.decode()
    m2 = re.search(r'<input[^>]*name="veg"[^>]*>', html2)
    assert m2 is not None
    assert "checked" not in m2.group(0)


@pytest.mark.django_db
def test_form_values_are_filled_from_the_address(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(name_en="Prod", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p, is_active=True)
    response = client.get("/products/?min_price=50&max_price=150&in_stock=1")
    html = response.content.decode()
    assert 'value="50"' in html
    assert 'value="150"' in html
    assert "checked" in html


@pytest.mark.django_db
def test_sort_select_is_inside_the_form_and_keeps_its_selection(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(name_en="Prod", brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p, is_active=True)
    response = client.get("/products/?sort=price_desc")
    html = response.content.decode()
    start = html.index('<form id="listing-form"')
    end = html.index("</form>", start) + len("</form>")
    form_block = html[start:end]
    assert 'name="sort"' in form_block
    m = re.search(r'<option value="price_desc"[^>]*>', form_block)
    assert m is not None
    assert "selected" in m.group(0)
    m2 = re.search(r'<option value="name"[^>]*>', form_block)
    assert m2 is not None
    assert "selected" not in m2.group(0)
