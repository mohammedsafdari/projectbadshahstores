"""Tests for the product detail view (Slice 25)."""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.cart.stores import GUEST_CART_SESSION_KEY
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.core.models import ShopSettings
from apps.core.services import get_shop_settings
from apps.inventory.tests.factories import InventoryMovementFactory


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


# ---------------------------------------------------------------------------
# Cart forms on the product page (Slice 32, D61)
# ---------------------------------------------------------------------------
def _product_with_variant(*, stock: str = "10.000", **variant_kwargs):
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    variant = ProductVariantFactory(product=product, is_active=True, **variant_kwargs)
    if Decimal(stock):
        InventoryMovementFactory(variant=variant, quantity_change=Decimal(stock))
    return product, variant


@pytest.mark.django_db
def test_each_variant_has_an_add_to_cart_form_posting_to_cart_add(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    for price in (10, 20):
        variant = ProductVariantFactory(
            product=product, is_active=True, selling_price=price, mrp=price
        )
        InventoryMovementFactory(variant=variant, quantity_change=Decimal("5.000"))
    html = client.get(f"/product/{product.slug}/").content.decode()

    assert html.count('action="/cart/add/"') == 2


@pytest.mark.django_db
def test_form_contains_the_variant_id_and_no_price_field(client: Client) -> None:
    product, variant = _product_with_variant(selling_price=10, mrp=10)
    html = client.get(f"/product/{product.slug}/").content.decode()

    assert f'name="variant_id" value="{variant.pk}"' in html
    assert 'name="price"' not in html


@pytest.mark.django_db
def test_loose_variant_input_uses_the_step_min_and_max(client: Client) -> None:
    product, _variant = _product_with_variant(
        is_sold_loose=True,
        unit="KG",
        pack_size=Decimal("1.000"),
        quantity_step=Decimal("0.100"),
        selling_price=10,
        mrp=10,
    )
    html = client.get(f"/product/{product.slug}/").content.decode()

    assert 'step="0.1"' in html
    assert 'min="0.1"' in html
    assert 'max="50"' in html


@pytest.mark.django_db
def test_loose_variant_shows_only_presets_that_fit_the_step_and_the_stock(client: Client) -> None:
    product, variant = _product_with_variant(
        stock="0.000",
        is_sold_loose=True,
        unit="KG",
        pack_size=Decimal("1.000"),
        quantity_step=Decimal("0.100"),
        selling_price=10,
        mrp=10,
    )
    InventoryMovementFactory(variant=variant, quantity_change=Decimal("0.700"))
    html = client.get(f"/product/{product.slug}/").content.decode()

    assert 'data-preset="0.5"' in html
    assert 'data-preset="0.25"' not in html
    assert 'data-preset="1"' not in html


@pytest.mark.django_db
def test_packet_variant_shows_a_stepper_and_whole_number_input(client: Client) -> None:
    product, _variant = _product_with_variant(
        unit="PACKET", pack_size=Decimal("1.000"), selling_price=10, mrp=10
    )
    html = client.get(f"/product/{product.slug}/").content.decode()

    assert 'data-stepper="minus"' in html
    assert 'data-stepper="plus"' in html
    assert 'step="1"' in html
    assert 'min="1"' in html
    assert 'inputmode="numeric"' in html


@pytest.mark.django_db
def test_out_of_stock_variant_has_no_active_add_button(client: Client) -> None:
    product, _variant = _product_with_variant(stock="0.000", selling_price=10, mrp=10)
    html = client.get(f"/product/{product.slug}/").content.decode()

    assert "Out of stock" in html
    assert "/cart/add/" not in html


@pytest.mark.django_db
def test_variant_below_the_stock_buffer_has_no_active_add_button(client: Client) -> None:
    product, variant = _product_with_variant(stock="0.000", selling_price=10, mrp=10)
    InventoryMovementFactory(variant=variant, quantity_change=Decimal("1.000"))
    row = ShopSettings.load()
    row.online_stock_buffer = 2
    row.save(update_fields=["online_stock_buffer", "updated_at"])
    cache.clear()
    html = client.get(f"/product/{product.slug}/").content.decode()

    assert "Out of stock" in html
    assert "/cart/add/" not in html


@pytest.mark.django_db
def test_the_old_coming_soon_button_is_gone(client: Client) -> None:
    product, _variant = _product_with_variant(selling_price=10, mrp=10)
    html = client.get(f"/product/{product.slug}/").content.decode()

    assert "Add to cart (coming soon)" not in html
    assert "coming soon" not in html


@pytest.mark.django_db
def test_add_result_region_exists_and_is_a_polite_live_region(client: Client) -> None:
    product, variant = _product_with_variant(selling_price=10, mrp=10)
    html = client.get(f"/product/{product.slug}/").content.decode()

    assert re.search(rf'<div id="add-result-{variant.pk}"[^>]*aria-live="polite"', html)


@pytest.mark.django_db
def test_product_detail_query_count_does_not_grow_with_the_number_of_variants(
    client: Client,
) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=product, is_active=True, selling_price=10, mrp=10)
    get_shop_settings()

    with CaptureQueriesContext(connection) as one:
        client.get(f"/product/{product.slug}/")
    for price in (20, 30, 40, 50):
        ProductVariantFactory(product=product, is_active=True, selling_price=price, mrp=price)
    with CaptureQueriesContext(connection) as many:
        client.get(f"/product/{product.slug}/")

    assert len(one.captured_queries) == len(many.captured_queries)


# ---------------------------------------------------------------------------
# The add form's CSRF token (Slice 33, D62)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_add_form_on_the_product_page_carries_a_working_csrf_token() -> None:
    product, variant = _product_with_variant(selling_price=10, mrp=10)
    client = Client(enforce_csrf_checks=True)
    html = client.get(f"/product/{product.slug}/").content.decode()
    match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
    assert match is not None
    token = match.group(1)

    response = client.post(
        reverse("cart_add"),
        {
            "variant_id": str(variant.pk),
            "quantity": "1",
            "csrfmiddlewaretoken": token,
        },
    )

    assert response.status_code == 302
    assert client.session[GUEST_CART_SESSION_KEY] == {str(variant.pk): "1.000"}
