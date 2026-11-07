"""Tests for the checkout page and its double-submit guard (D66)."""

from __future__ import annotations

import re
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Address, User
from apps.accounts.services import create_address
from apps.cart.models import CartItem
from apps.cart.services import add_item, build_cart
from apps.cart.stores import DbCartStore
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.core.models import ShopSettings
from apps.delivery.services import get_delivery_promise
from apps.delivery.tests.factories import DeliveryZoneFactory, PinCodeZoneFactory
from apps.inventory.models import StockReservation
from apps.inventory.services import adjust_stock, receive_stock
from apps.orders.models import Order
from apps.orders.presentation import (
    ADDRESS_INVALID,
    ADDRESS_NOT_FOUND_MESSAGE,
    ADDRESS_NOT_SERVED_MESSAGE,
    GROUP_INVALID_MESSAGE,
    NO_SERVED_ADDRESS,
    UNSERVED_NOTE,
    cod_limit_message,
    cod_sentence,
)
from apps.orders.tests.factories import OrderFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PINCODE = "452012"


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before every test."""
    cache.clear()
    ShopSettings.load()
    yield
    cache.clear()


def _set_settings(**values: object) -> None:
    row = ShopSettings.load()
    for name, value in values.items():
        setattr(row, name, value)
    # update_fields takes a list of names; the dict's keys are those names.
    row.save(update_fields=[*values, "updated_at"])


def _address(
    user: User,
    *,
    pincode: str = PINCODE,
    label: str = "HOME",
) -> Address:
    return create_address(
        user,
        recipient_name="Asha Sharma",
        recipient_phone="+919876543210",
        line1="12 Test Road",
        line2="Near the park",
        landmark="Opposite the school",
        city="Indore",
        state="Madhya Pradesh",
        pincode=pincode,
        label=label,
    )


def _serve(pincode: str = PINCODE) -> None:
    zone = DeliveryZoneFactory()
    PinCodeZoneFactory(zone=zone, pincode=pincode)


def _variant(
    *,
    stock: str = "10.000",
    price: str = "100.00",
    mrp: str = "120.00",
) -> ProductVariant:
    variant = ProductVariantFactory(
        is_sold_loose=False,
        unit=ProductVariant.Unit.PACKET,
        pack_size=Decimal("1.000"),
        quantity_step=Decimal("1"),
        selling_price=Decimal(price),
        mrp=Decimal(mrp),
    )
    receive_stock(variant=variant, quantity=Decimal(stock))
    return variant


def _fill_cart(user: User, variant: ProductVariant, quantity: str = "2") -> None:
    add_item(DbCartStore(user), variant, Decimal(quantity))


def _strip_tags(fragment: str) -> str:
    """Remove inner tags so a container can be compared as plain text."""
    return re.sub(r"<[^>]+>", "", fragment)


def _element_text(html: str, marker: str) -> str:
    """The text inside the first element that carries this data marker."""
    # (?P=tag) makes the closing tag match the opening one.
    pattern = (
        rf"<(?P<tag>[a-z0-9]+)\b[^>]*\b{re.escape(marker)}\b[^>]*>"
        rf"(?P<body>.*?)</(?P=tag)>"
    )
    match = re.search(pattern, html, re.DOTALL)
    assert match is not None, f"No element with {marker}."
    return _strip_tags(match.group("body")).strip()


def _has_element(html: str, marker: str) -> bool:
    """Whether any opening tag carries this data marker."""
    return re.search(rf"<[a-z0-9]+\b[^>]*\b{re.escape(marker)}\b", html) is not None


def _message_text(html: str) -> str:
    """The text of the first flash message element on the page."""
    # The base template has no data marker on messages, so the class name is
    # the only anchor; [^"]* allows the extra tag class (for example "error").
    match = re.search(r'<li class="message[^"]*">(.*?)</li>', html, re.DOTALL)
    assert match is not None, "No message on the page."
    return _strip_tags(match.group(1)).strip()


def _inputs(html: str, name: str) -> list[dict[str, object]]:
    """Every input with this name, its attributes and its boolean flags."""
    rows: list[dict[str, object]] = []
    for tag in re.findall(r"<input\b[^>]*>", html):
        attrs = dict(re.findall(r'([a-zA-Z0-9_:-]+)="([^"]*)"', tag))
        if attrs.get("name") != name:
            continue
        attrs["checked"] = " checked" in tag
        attrs["disabled"] = " disabled" in tag
        rows.append(attrs)
    return rows


def _form_with_action(html: str, action: str) -> str:
    """Return the first complete <form> whose action is `action`.

    This mirrors the helper in apps/cart/tests/test_views_cart.py: a test must
    read inside the specific form, never the whole page.
    """
    pattern = rf'<form[^>]*action="{re.escape(action)}"[^>]*>.*?</form>'
    match = re.search(pattern, html, re.DOTALL)
    assert match is not None, f"No form with action {action}."
    return match.group(0)


def _csrf_token_from(form: str) -> str:
    """Read the CSRF token out of one form block."""
    match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', form)
    assert match is not None, "The form has no CSRF token."
    return match.group(1)


def _open_checkout(client: Client) -> tuple[str, str]:
    """GET the page and return its hidden group and its CSRF token."""
    html = client.get(reverse("checkout")).content.decode()
    form = _form_with_action(html, reverse("checkout"))
    groups = _inputs(html, "checkout_group")
    assert len(groups) == 1
    return str(groups[0]["value"]), _csrf_token_from(form)


def _post_data(
    group: str,
    token: str,
    address: Address,
    *,
    substitution: str = Order.SubstitutionPreference.CALL_ME,
    note: str = "",
) -> dict[str, str]:
    return {
        "csrfmiddlewaretoken": token,
        "checkout_group": group,
        "address": str(address.pk),
        "substitution_preference": substitution,
        "customer_note": note,
    }


def _ready_checkout() -> tuple[User, Address, ProductVariant, Client]:
    """One customer with a served address, stock and a ready cart."""
    user = UserFactory()
    _serve()
    address = _address(user)
    variant = _variant()
    _fill_cart(user, variant)
    client = Client()
    client.force_login(user)
    return user, address, variant, client


def test_checkout_redirects_a_guest_to_login_with_next() -> None:
    response = Client().get(reverse("checkout"))

    assert response.status_code == 302
    assert response.url == f"{reverse('account_login')}?next={reverse('checkout')}"


def test_checkout_with_an_empty_cart_redirects_to_the_cart() -> None:
    client = Client()
    client.force_login(UserFactory())

    response = client.get(reverse("checkout"))

    assert response.status_code == 302
    assert response.url == reverse("cart_detail")


def test_checkout_with_a_cart_below_the_minimum_redirects_to_the_cart() -> None:
    user = UserFactory()
    _serve()
    _address(user)
    variant = _variant()
    _fill_cart(user, variant, "1")
    client = Client()
    client.force_login(user)

    response = client.get(reverse("checkout"))

    assert response.status_code == 302
    assert response.url == reverse("cart_detail")


def test_checkout_without_an_address_redirects_to_add_address_with_next() -> None:
    user = UserFactory()
    _serve()
    variant = _variant()
    _fill_cart(user, variant)
    client = Client()
    client.force_login(user)

    response = client.get(reverse("checkout"))

    assert response.status_code == 302
    assert response.url == f"{reverse('address_add')}?next={reverse('checkout')}"


def test_checkout_page_lists_saved_addresses_with_the_default_selected() -> None:
    user = UserFactory()
    _serve()
    default = _address(user, label="HOME")
    other = _address(user, label="WORK")
    variant = _variant()
    _fill_cart(user, variant)
    client = Client()
    client.force_login(user)

    html = client.get(reverse("checkout")).content.decode()

    radios = _inputs(html, "address")
    assert len(radios) == 2
    by_value = {str(radio["value"]): radio for radio in radios}
    assert by_value[str(default.pk)]["checked"] is True
    assert by_value[str(other.pk)]["checked"] is False


def test_checkout_page_disables_an_unserved_address_with_a_notice() -> None:
    user = UserFactory()
    _serve()
    _address(user)
    unserved = _address(user, pincode="400001", label="WORK")
    variant = _variant()
    _fill_cart(user, variant)
    client = Client()
    client.force_login(user)

    html = client.get(reverse("checkout")).content.decode()

    radios = _inputs(html, "address")
    by_value = {str(radio["value"]): radio for radio in radios}
    assert by_value[str(unserved.pk)]["disabled"] is True
    assert _element_text(html, "data-unserved-note") == str(UNSERVED_NOTE)


def test_checkout_page_with_only_unserved_addresses_cannot_be_submitted() -> None:
    user = UserFactory()
    _serve()
    _address(user, pincode="400001")
    variant = _variant()
    _fill_cart(user, variant)
    client = Client()
    client.force_login(user)

    html = client.get(reverse("checkout")).content.decode()

    assert _has_element(html, "data-no-served-address")
    assert _element_text(html, "data-no-served-address") == str(NO_SERVED_ADDRESS)
    assert not _has_element(html, "data-place-order")


def test_checkout_page_shows_lines_totals_fee_and_the_cod_amount() -> None:
    _user, _address_row, _variant_row, client = _ready_checkout()
    _set_settings(
        minimum_order_value=Decimal("200.00"),
        delivery_fee=Decimal("30.00"),
        free_delivery_threshold=Decimal("500.00"),
        cod_cap=Decimal("2000.00"),
    )

    html = client.get(reverse("checkout")).content.decode()

    assert _element_text(html, "data-line-name") == _variant_row.product.name_en
    assert _element_text(html, "data-line-quantity") == "2 packs"
    assert _element_text(html, "data-line-total") == "₹200.00"
    assert _element_text(html, "data-item-total") == "₹200.00"
    assert _element_text(html, "data-delivery-fee") == "₹30.00"
    assert _element_text(html, "data-grand-total") == "₹230.00"
    assert _element_text(html, "data-cod-sentence") == cod_sentence(Decimal("230.00"))


def test_checkout_page_shows_the_delivery_day() -> None:
    _user, _address_row, _variant_row, client = _ready_checkout()

    html = client.get(reverse("checkout")).content.decode()

    assert _element_text(html, "data-delivery-day") == get_delivery_promise().label


def test_checkout_page_above_the_cod_cap_shows_the_limit_and_no_submit() -> None:
    _user, _address_row, _variant_row, client = _ready_checkout()
    _set_settings(cod_cap=Decimal("100.00"))

    html = client.get(reverse("checkout")).content.decode()

    assert _element_text(html, "data-cod-limit") == cod_limit_message(Decimal("100.00"))
    assert not _has_element(html, "data-place-order")


def test_checkout_page_creates_a_checkout_group_in_the_session() -> None:
    _user, _address_row, _variant_row, client = _ready_checkout()

    client.get(reverse("checkout"))

    group = client.session["checkout_group"]
    assert isinstance(group, str)
    assert UUID(group)


def test_checkout_page_keeps_the_same_group_on_reload() -> None:
    _user, _address_row, _variant_row, client = _ready_checkout()

    first = client.get(reverse("checkout")).content.decode()
    second = client.get(reverse("checkout")).content.decode()

    first_group = _inputs(first, "checkout_group")[0]["value"]
    second_group = _inputs(second, "checkout_group")[0]["value"]
    assert first_group == second_group
    assert client.session["checkout_group"] == first_group


def test_checkout_page_is_never_cached() -> None:
    _user, _address_row, _variant_row, client = _ready_checkout()

    response = client.get(reverse("checkout"))

    assert "no-store" in response["Cache-Control"]


def test_checkout_post_places_the_order_and_redirects_to_the_confirmation() -> None:
    _user, address, _variant_row, client = _ready_checkout()
    group, token = _open_checkout(client)

    response = client.post(reverse("checkout"), _post_data(group, token, address))

    order = Order.objects.get()
    assert response.status_code == 302
    assert response.url == reverse("order_confirmation", args=[order.checkout_group])
    assert order.status == Order.Status.CONFIRMED
    assert order.payment_method == Order.PaymentMethod.COD


def test_checkout_post_stores_the_substitution_preference_and_note() -> None:
    _user, address, _variant_row, client = _ready_checkout()
    group, token = _open_checkout(client)

    client.post(
        reverse("checkout"),
        _post_data(
            group,
            token,
            address,
            substitution=Order.SubstitutionPreference.REFUND,
            note="Leave it at the door.",
        ),
    )

    order = Order.objects.get()
    assert order.substitution_preference == Order.SubstitutionPreference.REFUND
    assert order.customer_note == "Leave it at the door."


def test_checkout_post_clears_the_cart() -> None:
    _user, address, _variant_row, client = _ready_checkout()
    group, token = _open_checkout(client)

    client.post(reverse("checkout"), _post_data(group, token, address))

    assert CartItem.objects.filter(cart__user=_user).count() == 0


def test_checkout_post_forgets_the_group_in_the_session() -> None:
    _user, address, _variant_row, client = _ready_checkout()
    group, token = _open_checkout(client)

    client.post(reverse("checkout"), _post_data(group, token, address))

    assert "checkout_group" not in client.session


def test_checkout_post_twice_with_the_same_group_creates_one_order() -> None:
    _user, address, _variant_row, client = _ready_checkout()
    group, token = _open_checkout(client)
    data = _post_data(group, token, address)

    client.post(reverse("checkout"), data)
    client.post(reverse("checkout"), data)

    assert Order.objects.count() == 1


def test_checkout_post_twice_redirects_to_the_same_confirmation() -> None:
    _user, address, _variant_row, client = _ready_checkout()
    group, token = _open_checkout(client)
    data = _post_data(group, token, address)

    first = client.post(reverse("checkout"), data)
    second = client.post(reverse("checkout"), data)

    assert first.status_code == 302
    assert second.status_code == 302
    assert first.url == second.url
    assert second.url == reverse("order_confirmation", args=[Order.objects.get().checkout_group])


def test_checkout_post_with_a_group_that_is_not_the_session_group_is_refused() -> None:
    _user, address, _variant_row, client = _ready_checkout()
    _original, token = _open_checkout(client)
    other_group = str(uuid4())

    response = client.post(reverse("checkout"), _post_data(other_group, token, address))

    assert response.status_code == 302
    assert response.url == reverse("checkout")
    assert Order.objects.count() == 0
    assert client.session["checkout_group"] != other_group


def test_checkout_post_with_another_users_group_is_refused() -> None:
    owner = UserFactory()
    foreign_group = uuid4()
    OrderFactory(user=owner, checkout_group=foreign_group)
    _user, address, _variant_row, client = _ready_checkout()

    response = client.post(
        reverse("checkout"),
        {
            "checkout_group": str(foreign_group),
            "address": str(address.pk),
            "substitution_preference": Order.SubstitutionPreference.CALL_ME,
            "customer_note": "",
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("checkout")
    assert Order.objects.count() == 1
    assert Order.objects.get().user_id == owner.pk


def test_checkout_post_with_an_unserved_address_shows_the_error_and_creates_no_order() -> None:
    _user, _address_row, _variant_row, client = _ready_checkout()
    unserved = _address(_user, pincode="400001", label="WORK")
    group, token = _open_checkout(client)

    response = client.post(reverse("checkout"), _post_data(group, token, unserved))

    assert response.status_code == 200
    assert str(ADDRESS_NOT_SERVED_MESSAGE) in _element_text(
        response.content.decode(), "data-form-errors"
    )
    assert Order.objects.count() == 0


def test_checkout_post_with_another_users_address_shows_the_error() -> None:
    _user, _address_row, _variant_row, client = _ready_checkout()
    other_user = UserFactory()
    foreign_address = _address(other_user)
    group, token = _open_checkout(client)

    response = client.post(reverse("checkout"), _post_data(group, token, foreign_address))

    assert response.status_code == 200
    assert str(ADDRESS_NOT_FOUND_MESSAGE) in _element_text(
        response.content.decode(), "data-form-errors"
    )
    assert Order.objects.count() == 0


def test_checkout_post_with_short_stock_redirects_to_the_cart_and_creates_no_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, address, variant, client = _ready_checkout()
    group, token = _open_checkout(client)
    # The page saw an OK line; the stock disappears before the POST reaches
    # reserve_stock, exactly like the service's own shortfall test.
    stale_summary = build_cart(DbCartStore(user))
    adjust_stock(variant=variant, quantity_change=Decimal("-9"), reason="Test")
    monkeypatch.setattr("apps.orders.services.build_cart", lambda store: stale_summary)

    response = client.post(reverse("checkout"), _post_data(group, token, address))

    assert response.status_code == 302
    assert response.url == reverse("cart_detail")
    assert Order.objects.count() == 0
    assert StockReservation.objects.count() == 0


def test_checkout_post_above_the_cod_cap_shows_the_error_and_creates_no_order() -> None:
    _user, address, _variant_row, client = _ready_checkout()
    _set_settings(cod_cap=Decimal("100.00"))
    group, token = _open_checkout(client)

    response = client.post(reverse("checkout"), _post_data(group, token, address))

    assert response.status_code == 200
    assert cod_limit_message(Decimal("100.00")) in _element_text(
        response.content.decode(), "data-form-errors"
    )
    assert Order.objects.count() == 0


def test_checkout_post_with_an_invalid_form_shows_errors_and_creates_no_order() -> None:
    _user, address, _variant_row, client = _ready_checkout()
    group, token = _open_checkout(client)
    data = _post_data(group, token, address)
    data["address"] = "not-a-number"

    response = client.post(reverse("checkout"), data)

    assert response.status_code == 200
    assert str(ADDRESS_INVALID) in _element_text(response.content.decode(), "data-form-errors")
    assert Order.objects.count() == 0


def test_checkout_post_needs_a_csrf_token() -> None:
    user = UserFactory()
    _serve()
    address = _address(user)
    variant = _variant()
    _fill_cart(user, variant)
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    group, _token = _open_checkout(client)

    response = client.post(
        reverse("checkout"),
        {
            "checkout_group": group,
            "address": str(address.pk),
            "substitution_preference": Order.SubstitutionPreference.CALL_ME,
            "customer_note": "",
        },
    )

    assert response.status_code == 403
    assert Order.objects.count() == 0


def test_checkout_post_by_a_guest_redirects_to_login() -> None:
    response = Client().post(reverse("checkout"), {})

    assert response.status_code == 302
    assert response.url.startswith(f"{reverse('account_login')}?next=")


def test_checkout_get_does_not_create_an_order() -> None:
    _user, _address_row, _variant_row, client = _ready_checkout()

    response = client.get(reverse("checkout"))

    assert response.status_code == 200
    assert Order.objects.count() == 0


def test_checkout_post_holds_stock_for_fulfilment() -> None:
    _user, address, variant, client = _ready_checkout()
    group, token = _open_checkout(client)

    client.post(reverse("checkout"), _post_data(group, token, address))

    order = Order.objects.get()
    holds = list(StockReservation.objects.filter(checkout_group=order.checkout_group))
    assert len(holds) == 1
    assert holds[0].variant_id == variant.pk
    assert holds[0].quantity == Decimal("2.000")
    assert holds[0].kind == StockReservation.Kind.HELD_FOR_FULFILMENT
    assert holds[0].status == StockReservation.Status.ACTIVE


def test_checkout_post_with_a_garbage_group_is_refused_with_the_invalid_group_message() -> None:
    _user, address, _variant_row, client = _ready_checkout()
    _group, token = _open_checkout(client)

    response = client.post(reverse("checkout"), _post_data("not-a-uuid", token, address))

    assert response.status_code == 302
    assert response.url == reverse("checkout")
    # The message is stored by the POST and rendered by the next page.
    html = client.get(reverse("checkout")).content.decode()
    assert _message_text(html) == str(GROUP_INVALID_MESSAGE)
    assert Order.objects.count() == 0
    assert StockReservation.objects.count() == 0


def test_checkout_page_exactly_at_the_cod_cap_still_shows_submit() -> None:
    user, _address_row, _variant_row, client = _ready_checkout()
    # Tie the cap to the cart's own total, so the comparison is exactly equal.
    summary = build_cart(DbCartStore(user))
    _set_settings(cod_cap=summary.grand_total)

    html = client.get(reverse("checkout")).content.decode()

    assert _has_element(html, "data-place-order")
    assert not _has_element(html, "data-cod-limit")
