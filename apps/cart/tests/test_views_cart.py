"""Tests for the cart page (D61)."""

from __future__ import annotations

import re
from decimal import Decimal
from html.parser import HTMLParser

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import Address
from apps.cart.stores import GUEST_CART_SESSION_KEY
from apps.cart.tests.factories import CartFactory, CartItemFactory
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductFactory, ProductVariantFactory
from apps.core.models import ShopSettings
from apps.core.services import get_shop_settings
from apps.inventory.tests.factories import InventoryMovementFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    cache.clear()
    yield
    cache.clear()


def _variant(
    *,
    stock: str = "10.000",
    price: str = "100.00",
    loose: bool = False,
    step: str = "1",
    active: bool = True,
    name: str | None = None,
    net_label: str = "",
) -> ProductVariant:
    product_kwargs = {"name_en": name} if name is not None else {}
    product = ProductFactory(is_active=True, **product_kwargs)
    variant = ProductVariantFactory(
        product=product,
        is_active=active,
        is_sold_loose=loose,
        unit=ProductVariant.Unit.KG if loose else ProductVariant.Unit.PACKET,
        pack_size=Decimal("1.000"),
        quantity_step=Decimal(step),
        selling_price=Decimal(price),
        mrp=Decimal(price),
        net_quantity_label=net_label,
    )
    if Decimal(stock):
        InventoryMovementFactory(variant=variant, quantity_change=Decimal(stock))
    return variant


def _guest_with(client: Client, lines: list[tuple[ProductVariant, str]]) -> None:
    session = client.session
    session[GUEST_CART_SESSION_KEY] = {str(variant.pk): quantity for variant, quantity in lines}
    session.save()


def _db_cart(user, lines: list[tuple[ProductVariant, str]]) -> None:
    cart = CartFactory(user=user)
    for variant, quantity in lines:
        CartItemFactory(cart=cart, variant=variant, quantity=Decimal(quantity))


def _set_settings(
    delivery_fee: str = "45.00",
    threshold: str = "800.00",
    minimum: str = "150.00",
) -> None:
    row = ShopSettings.load()
    row.delivery_fee = Decimal(delivery_fee)
    row.free_delivery_threshold = Decimal(threshold)
    row.minimum_order_value = Decimal(minimum)
    row.save(
        update_fields=[
            "delivery_fee",
            "free_delivery_threshold",
            "minimum_order_value",
            "updated_at",
        ]
    )


class _CartBodyInspector(HTMLParser):
    """Records whether the "Check delivery" text sits inside #cart-body."""

    def __init__(self) -> None:
        super().__init__()
        self.div_depth = 0
        self.cart_body_depth: int | None = None
        self.delivery_seen = False
        self.delivery_inside_cart_body = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "div":
            return
        # dict() turns the attribute pairs into a lookup table by name.
        if dict(attrs).get("id") == "cart-body":
            self.cart_body_depth = self.div_depth
        self.div_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "div":
            self.div_depth -= 1

    def handle_data(self, data: str) -> None:
        if "Check delivery" not in data:
            return
        self.delivery_seen = True
        if self.cart_body_depth is not None and self.div_depth > self.cart_body_depth:
            self.delivery_inside_cart_body = True


def test_cart_page_is_available_to_a_guest() -> None:
    assert Client().get(reverse("cart_detail")).status_code == 200


def test_empty_cart_shows_the_empty_state_and_a_shop_link() -> None:
    html = Client().get(reverse("cart_detail")).content.decode()

    assert "Your cart is empty." in html
    assert 'href="/products/"' in html


def test_cart_page_lists_lines_with_name_pack_label_price_and_line_total() -> None:
    variant = _variant(name="Atta", net_label="500 g")
    client = Client()
    _guest_with(client, [(variant, "2.000")])

    html = client.get(reverse("cart_detail")).content.decode()

    assert "Atta" in html
    assert "500 g" in html
    assert "₹100.00" in html
    assert "₹200.00" in html


def test_cart_page_shows_the_item_total_fee_and_grand_total() -> None:
    _set_settings()
    variant = _variant(price="100.00")
    client = Client()
    _guest_with(client, [(variant, "2.000")])

    html = client.get(reverse("cart_detail")).content.decode()

    assert "₹200.00" in html
    assert "₹45.00" in html
    assert "₹245.00" in html


def test_cart_page_shows_free_delivery_when_the_threshold_is_met() -> None:
    _set_settings()
    variant = _variant(price="800.00")
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    html = client.get(reverse("cart_detail")).content.decode()

    assert "Free" in html


def test_cart_page_shows_how_much_more_for_free_delivery() -> None:
    _set_settings()
    variant = _variant(price="300.00")
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    html = client.get(reverse("cart_detail")).content.decode()

    assert "Add ₹500.00 more for free delivery." in html


def test_cart_page_shows_the_minimum_order_warning_with_the_missing_amount() -> None:
    _set_settings()
    variant = _variant(price="100.00")
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    html = client.get(reverse("cart_detail")).content.decode()

    assert "Minimum order is ₹150.00. Add ₹50.00 more." in html


def test_cart_page_hides_the_minimum_warning_when_met() -> None:
    _set_settings()
    variant = _variant(price="200.00")
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    html = client.get(reverse("cart_detail")).content.decode()

    assert "Minimum order is" not in html


def test_cart_page_flags_an_out_of_stock_line() -> None:
    variant = _variant(stock="0.000")
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    html = client.get(reverse("cart_detail")).content.decode()

    assert "Out of stock right now. Please remove it." in html


def test_cart_page_flags_a_line_above_the_available_amount_with_the_amount() -> None:
    variant = _variant(stock="2.000")
    client = Client()
    _guest_with(client, [(variant, "5.000")])

    html = client.get(reverse("cart_detail")).content.decode()

    assert "Only 2 available." in html


def test_cart_page_flags_an_archived_variant_line_with_a_remove_button() -> None:
    variant = _variant(active=False, stock="0.000")
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    html = client.get(reverse("cart_detail")).content.decode()

    assert "No longer available. Please remove it." in html
    assert 'action="/cart/remove/"' in html
    assert "Remove" in html


def test_cart_page_flags_a_bad_quantity_line() -> None:
    variant = _variant()
    client = Client()
    _guest_with(client, [(variant, "1.500")])

    html = client.get(reverse("cart_detail")).content.decode()

    assert "This quantity is not valid for this item." in html


def test_flagged_lines_are_not_in_the_totals_shown() -> None:
    _set_settings()
    ok_variant = _variant(price="100.00")
    bad_variant = _variant(price="500.00", stock="0.000")
    client = Client()
    _guest_with(client, [(ok_variant, "1.000"), (bad_variant, "1.000")])

    html = client.get(reverse("cart_detail")).content.decode()

    assert "₹100.00" in html
    assert "₹145.00" in html
    assert "₹645.00" not in html


def test_checkout_is_not_offered_yet() -> None:
    variant = _variant()
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    html = client.get(reverse("cart_detail")).content.decode()

    assert "Checkout (coming soon)" in html
    assert "disabled" in html
    assert "/checkout/" not in html


def test_guest_sees_a_login_hint_with_next_back_to_the_cart() -> None:
    html = Client().get(reverse("cart_detail")).content.decode()

    assert "/account/login/?next=%2Fcart%2F" in html


def test_logged_in_user_does_not_see_the_login_hint() -> None:
    user = UserFactory()
    client = Client()
    client.force_login(user)

    html = client.get(reverse("cart_detail")).content.decode()

    assert "to keep your cart" not in html


def test_cart_page_is_noindex() -> None:
    html = Client().get(reverse("cart_detail")).content.decode()

    assert '<meta name="robots" content="noindex">' in html


def test_cart_page_is_not_cacheable() -> None:
    response = Client().get(reverse("cart_detail"))

    assert "no-store" in response["Cache-Control"]


def test_cart_page_query_count_does_not_grow_with_the_number_of_lines() -> None:
    variants = [_variant() for _ in range(10)]
    client = Client()
    _guest_with(client, [(variants[0], "1.000")])
    # A warm settings cache keeps the first read from adding a create query.
    get_shop_settings()

    with CaptureQueriesContext(connection) as one:
        client.get(reverse("cart_detail"))
    _guest_with(client, [(variant, "1.000") for variant in variants])
    with CaptureQueriesContext(connection) as ten:
        client.get(reverse("cart_detail"))

    assert len(one.captured_queries) == len(ten.captured_queries)


def test_two_customers_see_only_their_own_cart() -> None:
    first = _variant(name="FirstOnly")
    second = _variant(name="SecondOnly")
    first_user = UserFactory()
    second_user = UserFactory()
    _db_cart(first_user, [(first, "1.000")])
    _db_cart(second_user, [(second, "1.000")])
    client = Client()

    client.force_login(first_user)
    first_html = client.get(reverse("cart_detail")).content.decode()
    client.force_login(second_user)
    second_html = client.get(reverse("cart_detail")).content.decode()

    assert "FirstOnly" in first_html
    assert "SecondOnly" not in first_html
    assert "SecondOnly" in second_html
    assert "FirstOnly" not in second_html


def test_cart_page_escapes_product_names() -> None:
    variant = _variant(name="<script>alert(1)</script>")
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    html = client.get(reverse("cart_detail")).content.decode()

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html


def test_quantity_input_for_loose_has_the_step_and_min_from_the_variant() -> None:
    variant = _variant(loose=True, step="0.100", stock="10.000")
    client = Client()
    _guest_with(client, [(variant, "0.500")])

    html = client.get(reverse("cart_detail")).content.decode()

    assert 'step="0.1"' in html
    assert 'min="0.1"' in html


def test_quantity_input_for_packets_is_whole_numbers() -> None:
    variant = _variant()
    client = Client()
    _guest_with(client, [(variant, "2.000")])

    html = client.get(reverse("cart_detail")).content.decode()

    assert 'step="1"' in html
    assert 'min="1"' in html
    assert 'inputmode="numeric"' in html


def test_cart_page_lines_keep_the_order_they_were_added_in() -> None:
    first = _variant(name="First")
    second = _variant(name="Second")
    third = _variant(name="Third")
    client = Client()
    _guest_with(client, [(first, "1.000"), (second, "1.000"), (third, "1.000")])

    html = client.get(reverse("cart_detail")).content.decode()

    order = [int(pk) for pk in re.findall(r'data-variant-id="(\d+)"', html)]
    assert order == [first.pk, second.pk, third.pk]


# ---------------------------------------------------------------------------
# Delivery check box and the Slice 32 review fixes (Slice 33, D62)
# ---------------------------------------------------------------------------
def test_cart_page_has_the_delivery_check_box_outside_the_cart_body() -> None:
    html = Client().get(reverse("cart_detail")).content.decode()
    inspector = _CartBodyInspector()
    inspector.feed(html)

    assert inspector.delivery_seen is True
    assert inspector.delivery_inside_cart_body is False


def test_the_delivery_check_box_is_not_part_of_the_htmx_cart_fragment() -> None:
    variant = _variant()
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    response = client.post(
        reverse("cart_update"),
        {"variant_id": str(variant.pk), "quantity": "2"},
        HTTP_HX_REQUEST="true",
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "Check delivery" not in html
    assert 'id="delivery-result"' not in html


def test_delivery_box_is_prefilled_with_the_default_address_pin_code_for_a_customer() -> None:
    user = UserFactory()
    Address.objects.create(
        user=user,
        recipient_name="Asha Sharma",
        recipient_phone="+919876543210",
        line1="12, Saifee Nagar",
        pincode="452012",
        is_default=True,
    )
    Address.objects.create(
        user=user,
        recipient_name="Asha Sharma",
        recipient_phone="+919876543210",
        line1="1, Another Road",
        pincode="452099",
    )
    client = Client()
    client.force_login(user)

    html = client.get(reverse("cart_detail")).content.decode()

    assert re.search(r'id="delivery-pincode"[^>]*value="452012"', html)


def test_delivery_box_is_empty_for_a_guest() -> None:
    html = Client().get(reverse("cart_detail")).content.decode()

    assert re.search(r'id="delivery-pincode"[^>]*value=""', html)


def test_delivery_box_posts_by_get_to_the_delivery_check_url() -> None:
    html = Client().get(reverse("cart_detail")).content.decode()

    assert 'method="get"' in html
    assert 'action="/delivery/check/"' in html
    assert 'hx-get="/delivery/check/"' in html


def test_cart_page_hides_the_free_delivery_hint_when_delivery_is_free() -> None:
    _set_settings(threshold="500.00")
    variant = _variant(price="500.00")
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    html = client.get(reverse("cart_detail")).content.decode()

    assert "more for free delivery" not in html


def test_login_hint_after_an_htmx_update_links_back_to_the_cart_page() -> None:
    variant = _variant()
    client = Client()
    _guest_with(client, [(variant, "1.000")])

    html = client.post(
        reverse("cart_update"),
        {"variant_id": str(variant.pk), "quantity": "2"},
        HTTP_HX_REQUEST="true",
    ).content.decode()

    assert "/account/login/?next=%2Fcart%2F" in html
    assert "%2Fcart%2Fupdate%2F" not in html


def test_update_form_on_the_cart_page_carries_a_working_csrf_token() -> None:
    variant = _variant()
    client = Client(enforce_csrf_checks=True)
    _guest_with(client, [(variant, "1.000")])
    html = client.get(reverse("cart_detail")).content.decode()
    match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
    assert match is not None
    token = match.group(1)

    response = client.post(
        reverse("cart_update"),
        {
            "variant_id": str(variant.pk),
            "quantity": "2",
            "csrfmiddlewaretoken": token,
        },
    )

    assert response.status_code == 302
    assert client.session[GUEST_CART_SESSION_KEY] == {str(variant.pk): "2.000"}
