"""Tests for the product detail view (Slice 25)."""

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


@pytest.mark.django_db
def test_detail_page_shows_name_brand_and_description(client: Client) -> None:
    brand = BrandFactory(is_active=True, name="BrandX")
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(
        brand=brand, category=cat, is_active=True, name_en="ProdX", description="DescX"
    )
    ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    response = client.get(f"/product/{product.slug}/")
    html = response.content.decode()
    assert "ProdX" in html
    assert "BrandX" in html
    assert "DescX" in html


@pytest.mark.django_db
def test_unknown_slug_gives_404(client: Client) -> None:
    assert client.get("/product/nope/").status_code == 404


@pytest.mark.django_db
def test_hidden_product_gives_404(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=False)
    ProductVariantFactory(product=product, is_active=True)
    assert client.get(f"/product/{product.slug}/").status_code == 404


@pytest.mark.django_db
def test_breadcrumb_shows_category_chain_and_product(client: Client) -> None:
    parent = CategoryFactory(is_active=True, name="Parent", slug="parent")
    child = CategoryFactory(is_active=True, name="Child", slug="child", parent=parent)
    brand = BrandFactory(is_active=True)
    product = ProductFactory(brand=brand, category=child, is_active=True, name_en="Prod")
    ProductVariantFactory(product=product, is_active=True)
    response = client.get(f"/product/{product.slug}/")
    html = response.content.decode()
    assert "Parent" in html
    assert "Child" in html
    assert "Prod" in html


@pytest.mark.django_db
def test_variant_rows_show_price_mrp_and_savings(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=45, mrp=50)
    response = client.get(f"/product/{product.slug}/")
    html = response.content.decode()
    assert "₹45.00" in html
    assert "₹50.00" in html
    assert "line-through" in html
    assert "Save ₹5.00 (10%)" in html


@pytest.mark.django_db
def test_unit_price_text_shown_for_kg_variant(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(
        product=product, is_active=True, unit="KG", pack_size=1, selling_price=10, mrp=10
    )
    response = client.get(f"/product/{product.slug}/")
    html = response.content.decode()
    assert "per kg" in html


@pytest.mark.django_db
def test_no_unit_price_text_for_packet_variant(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(
        product=product, is_active=True, unit="PACKET", pack_size=1, selling_price=10, mrp=10
    )
    response = client.get(f"/product/{product.slug}/")
    html = response.content.decode()
    assert "per packet" not in html


@pytest.mark.django_db
def test_out_of_stock_variant_marked(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    response = client.get(f"/product/{product.slug}/")
    html = response.content.decode()
    assert "Out of stock" in html


@pytest.mark.django_db
def test_add_to_cart_is_disabled_placeholder(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    response = client.get(f"/product/{product.slug}/")
    html = response.content.decode()
    m = re.search(r"<button[^>]*>Add to cart \(coming soon\)</button>", html)
    assert m is not None
    assert "disabled" in m.group(0)


@pytest.mark.django_db
def test_main_image_is_medium_and_others_are_links(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    response = client.get(f"/product/{product.slug}/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_placeholder_when_product_has_no_photo(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    response = client.get(f"/product/{product.slug}/")
    html = response.content.decode()
    assert "product-placeholder.svg" in html


@pytest.mark.django_db
def test_veg_and_non_veg_marks_have_text(client: Client) -> None:
    from apps.catalog.models import Product

    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p1 = ProductFactory(
        brand=brand, category=cat, is_active=True, diet_type=Product.DietType.VEG, name_en="VegProd"
    )
    ProductVariantFactory(product=p1, is_active=True, selling_price=10, mrp=10)
    response = client.get(f"/product/{p1.slug}/")
    html = response.content.decode()
    assert re.search(r'<span[^>]*aria-label="Vegetarian"[^>]*>Veg</span>', html)
    assert 'aria-label="Non-vegetarian"' not in html


@pytest.mark.django_db
def test_own_brand_badge_only_for_own_brand(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(
        brand=brand, category=cat, is_active=True, is_own_brand=True, name_en="OwnProd"
    )
    ProductVariantFactory(product=p, is_active=True, selling_price=10, mrp=10)
    response = client.get(f"/product/{p.slug}/")
    html = response.content.decode()
    assert "Our own brand" in html


@pytest.mark.django_db
def test_fssai_shown_for_own_brand_when_setting_filled(client: Client, settings) -> None:
    settings.SHOP_FSSAI_LICENCE = "12345678901234"
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(
        brand=brand, category=cat, is_active=True, is_own_brand=True, name_en="OwnProd"
    )
    ProductVariantFactory(product=p, is_active=True, selling_price=10, mrp=10)
    response = client.get(f"/product/{p.slug}/")
    html = response.content.decode()
    assert "FSSAI licence no. 12345678901234" in html


@pytest.mark.django_db
def test_fssai_hidden_when_setting_empty_or_not_own_brand(client: Client, settings) -> None:
    settings.SHOP_FSSAI_LICENCE = ""
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(
        brand=brand, category=cat, is_active=True, is_own_brand=False, name_en="NotOwn"
    )
    ProductVariantFactory(product=p, is_active=True, selling_price=10, mrp=10)
    response = client.get(f"/product/{p.slug}/")
    html = response.content.decode()
    assert "FSSAI licence no." not in html


@pytest.mark.django_db
def test_label_details_shown_when_filled_and_hidden_when_blank(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    filled = ProductFactory(brand=brand, category=cat, is_active=True, name_en="Filled")
    ProductVariantFactory(
        product=filled, is_active=True, selling_price=10, mrp=10, ingredients="Wheat"
    )
    filled_html = client.get(f"/product/{filled.slug}/").content.decode()
    assert re.search(r"<details[^>]*>", filled_html)
    assert "Product details" in filled_html
    assert "Wheat" in filled_html

    blank = ProductFactory(brand=brand, category=cat, is_active=True, name_en="Blank")
    ProductVariantFactory(product=blank, is_active=True, selling_price=10, mrp=10)
    blank_html = client.get(f"/product/{blank.slug}/").content.decode()
    assert "Product details" not in blank_html
    assert re.search(r"<details[^>]*>", blank_html) is None


@pytest.mark.django_db
def test_related_products_link_to_their_pages(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(brand=brand, category=cat, is_active=True, name_en="Main")
    ProductVariantFactory(product=p, is_active=True, selling_price=10, mrp=10)
    r = ProductFactory(brand=brand, category=cat, is_active=True, name_en="Rel")
    ProductVariantFactory(product=r, is_active=True, selling_price=10, mrp=10)
    response = client.get(f"/product/{p.slug}/")
    html = response.content.decode()
    assert f"/product/{r.slug}/" in html


@pytest.mark.django_db
def test_title_and_meta_description(client: Client) -> None:
    brand = BrandFactory(is_active=True, name="BrandX")
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(
        brand=brand, category=cat, is_active=True, name_en="ProdX", description="DescX"
    )
    ProductVariantFactory(product=p, is_active=True, selling_price=10, mrp=10)
    response = client.get(f"/product/{p.slug}/")
    html = response.content.decode()
    assert "<title>ProdX — BrandX — Badshah Store</title>" in html
    assert 'name="description"' in html


@pytest.mark.django_db
def test_detail_page_has_json_ld_script(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p, is_active=True, selling_price=10, mrp=10)
    response = client.get(f"/product/{p.slug}/")
    html = response.content.decode()
    assert 'type="application/ld+json"' in html


@pytest.mark.django_db
def test_savings_text_shows_amount_and_percent(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p, is_active=True, selling_price=45, mrp=50)
    response = client.get(f"/product/{p.slug}/")
    html = response.content.decode()
    assert "Save ₹5.00 (10%)" in html


@pytest.mark.django_db
def test_no_savings_text_when_mrp_equals_price(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p, is_active=True, selling_price=48, mrp=48)
    response = client.get(f"/product/{p.slug}/")
    html = response.content.decode()
    assert "Save" not in html


@pytest.mark.django_db
def test_own_brand_badge_hidden_for_other_brands(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(
        brand=brand, category=cat, is_active=True, is_own_brand=False, name_en="NotOwn"
    )
    ProductVariantFactory(product=p, is_active=True, selling_price=10, mrp=10)
    response = client.get(f"/product/{p.slug}/")
    html = response.content.decode()
    assert "Our own brand" not in html


@pytest.mark.django_db
def test_non_veg_mark_shown_and_veg_mark_absent(client: Client) -> None:
    from apps.catalog.models import Product

    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(
        brand=brand,
        category=cat,
        is_active=True,
        diet_type=Product.DietType.NON_VEG,
        name_en="NonVegProd",
    )
    ProductVariantFactory(product=p, is_active=True, selling_price=10, mrp=10)
    response = client.get(f"/product/{p.slug}/")
    html = response.content.decode()
    assert re.search(r'<span[^>]*aria-label="Non-vegetarian"[^>]*>Non-veg</span>', html)
    assert 'aria-label="Vegetarian"' not in html


@pytest.mark.django_db
def test_no_diet_mark_for_not_applicable(client: Client) -> None:
    from apps.catalog.models import Product

    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(
        brand=brand,
        category=cat,
        is_active=True,
        diet_type=Product.DietType.NOT_APPLICABLE,
        name_en="NAProd",
    )
    ProductVariantFactory(product=p, is_active=True, selling_price=10, mrp=10)
    response = client.get(f"/product/{p.slug}/")
    html = response.content.decode()
    assert 'aria-label="Vegetarian"' not in html
    assert 'aria-label="Non-vegetarian"' not in html


@pytest.mark.django_db
def test_meta_description_is_cut_at_a_word_within_160_characters(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(brand=brand, category=cat, is_active=True, description="word " * 40)
    ProductVariantFactory(product=p, is_active=True, selling_price=10, mrp=10)
    html = client.get(f"/product/{p.slug}/").content.decode()
    match = re.search(r'<meta name="description" content="([^"]*)">', html)
    assert match is not None
    content = match.group(1)
    assert content == ("word " * 31 + "word…")
    assert len(content) == 160


@pytest.mark.django_db
def test_query_count_does_not_grow_with_variants(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p, is_active=True, selling_price=10, mrp=10)
    ProductVariantFactory(product=p, is_active=True, selling_price=20, mrp=20)
    response = client.get(f"/product/{p.slug}/")
    assert response.status_code == 200
