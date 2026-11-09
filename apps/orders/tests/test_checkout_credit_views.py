"""Tests for the credit option on the checkout and confirmation pages (D68)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.cart.models import CartItem
from apps.credit.models import CreditAccount, CreditEntry
from apps.inventory.models import StockReservation
from apps.orders.models import Order
from apps.orders.presentation import (
    CREDIT_UNAVAILABLE_MESSAGE,
    available_credit_sentence,
    cod_on_delivery_sentence,
    cod_sentence,
    credit_choice_sentence,
    credit_confirmation_sentence,
    no_credit_left_message,
    payment_method_label,
)
from apps.orders.tests.checkout_helpers import (
    clear_settings,
    element_text,
    has_element,
    inputs,
    open_checkout,
    place_cod,
    place_credit,
    post_data,
    ready_checkout,
    reset_settings,
    set_settings,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before every test."""
    reset_settings()
    yield
    clear_settings()


def _page(client) -> str:
    """The checkout page HTML for this logged-in client."""
    return client.get(reverse("checkout")).content.decode()


def _credit_box(html: str) -> dict[str, object]:
    """The one khata checkbox on the checkout page."""
    boxes = inputs(html, "use_credit")
    assert len(boxes) == 1, "The checkout page must show one khata checkbox."
    return boxes[0]


def test_checkout_page_offers_no_credit_to_a_customer_without_an_account() -> None:
    _user, _address_row, _variant_row, client = ready_checkout()

    html = _page(client)

    assert inputs(html, "use_credit") == []
    assert not has_element(html, "data-available-credit")
    assert not has_element(html, "data-credit-sentence")
    assert element_text(html, "data-cod-sentence") == cod_sentence(Decimal("230.00"))
    assert has_element(html, "data-place-order")


def test_checkout_page_offers_no_credit_to_a_suspended_account() -> None:
    _user, _address_row, _variant_row, client = ready_checkout(
        credit_limit="1000.00", credit_active=False
    )

    html = _page(client)

    assert inputs(html, "use_credit") == []
    assert not has_element(html, "data-available-credit")
    assert has_element(html, "data-place-order")


def test_checkout_page_offers_credit_with_the_available_amount() -> None:
    _user, _address_row, _variant_row, client = ready_checkout(
        credit_limit="1000.00", credit_balance="100.00"
    )

    html = _page(client)

    boxes = inputs(html, "use_credit")
    assert len(boxes) == 1
    assert boxes[0]["checked"] is True
    assert element_text(html, "data-available-credit") == available_credit_sentence(
        Decimal("900.00")
    )


def test_checkout_page_ticks_the_khata_box_by_default() -> None:
    _user, _address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")

    html = _page(client)

    assert _credit_box(html)["checked"] is True


def test_checkout_page_splits_the_order_when_credit_covers_only_part() -> None:
    _user, _address_row, _variant_row, client = ready_checkout(credit_limit="100.00")

    html = _page(client)

    assert _credit_box(html)["checked"] is True
    assert element_text(html, "data-credit-applied") == "₹100.00"
    assert element_text(html, "data-cash-due") == "₹130.00"
    assert element_text(html, "data-credit-sentence") == credit_choice_sentence(
        Decimal("100.00"), Decimal("130.00")
    )
    assert has_element(html, "data-place-order")


def test_checkout_page_with_credit_fully_covering_the_order_shows_no_cash_line() -> None:
    _user, _address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")
    set_settings(cod_cap=Decimal("100.00"))

    html = _page(client)

    assert _credit_box(html)["checked"] is True
    assert not has_element(html, "data-cash-due")
    assert not has_element(html, "data-cod-limit")
    assert has_element(html, "data-place-order")


def test_checkout_page_with_the_cash_part_above_the_cod_cap_cannot_be_submitted() -> None:
    _user, _address_row, _variant_row, client = ready_checkout(credit_limit="100.00")
    set_settings(cod_cap=Decimal("100.00"))

    html = _page(client)

    assert _credit_box(html)["checked"] is True
    assert has_element(html, "data-cod-limit")
    assert not has_element(html, "data-place-order")


def test_checkout_page_shows_the_khata_sentence_for_a_fully_covered_order() -> None:
    _user, _address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")

    html = _page(client)

    assert element_text(html, "data-credit-sentence") == credit_choice_sentence(
        Decimal("230.00"), Decimal("0.00")
    )


def test_checkout_post_with_credit_places_a_credit_order() -> None:
    _user, address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")
    group, token = open_checkout(client)

    response = client.post(
        reverse("checkout"),
        post_data(group, token, address_row, use_credit=True),
    )

    order = Order.objects.get()
    assert response.status_code == 302
    assert response.url == reverse("order_confirmation", args=[order.checkout_group])
    assert order.status == Order.Status.CONFIRMED
    assert order.payment_method == Order.PaymentMethod.CREDIT
    assert order.credit_applied == Decimal("230.00")


def test_checkout_post_with_credit_adds_to_the_balance() -> None:
    user, address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")
    group, token = open_checkout(client)

    client.post(
        reverse("checkout"),
        post_data(group, token, address_row, use_credit=True),
    )

    account = CreditAccount.objects.get(user=user)
    assert account.balance == Decimal("230.00")
    assert CreditEntry.objects.get(order__user=user).amount == Decimal("230.00")


def test_checkout_post_with_no_credit_left_shows_the_error_and_creates_nothing() -> None:
    user, address_row, _variant_row, client = ready_checkout(
        credit_limit="100.00", credit_balance="100.00"
    )
    group, token = open_checkout(client)

    response = client.post(
        reverse("checkout"),
        post_data(group, token, address_row, use_credit=True),
    )

    html = response.content.decode()
    assert response.status_code == 200
    assert element_text(html, "data-form-errors") == no_credit_left_message()
    assert Order.objects.count() == 0
    assert StockReservation.objects.count() == 0
    assert CreditEntry.objects.count() == 0
    assert CartItem.objects.filter(cart__user=user).count() == 1
    assert client.session["checkout_group"] == group


def test_checkout_post_with_credit_for_a_customer_without_an_account_shows_the_error() -> None:
    user, address_row, _variant_row, client = ready_checkout()
    group, token = open_checkout(client)

    response = client.post(
        reverse("checkout"),
        post_data(group, token, address_row, use_credit=True),
    )

    assert response.status_code == 200
    assert element_text(response.content.decode(), "data-form-errors") == str(
        CREDIT_UNAVAILABLE_MESSAGE
    )
    assert Order.objects.count() == 0
    assert CartItem.objects.filter(cart__user=user).count() == 1


def test_checkout_post_ignores_a_legacy_payment_method_field() -> None:
    _user, address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")
    group, token = open_checkout(client)
    data = post_data(group, token, address_row)
    # The form no longer has a payment_method field; an old client posting one
    # must not be able to choose anything the page did not offer.
    data["payment_method"] = "ONLINE"

    client.post(reverse("checkout"), data)

    assert Order.objects.get().payment_method == Order.PaymentMethod.COD


def test_checkout_post_defaults_to_cod_when_the_khata_box_is_not_sent() -> None:
    _user, address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")
    group, token = open_checkout(client)

    client.post(reverse("checkout"), post_data(group, token, address_row))

    assert Order.objects.get().payment_method == Order.PaymentMethod.COD


def test_checkout_post_with_a_split_order_charges_only_the_credit_part() -> None:
    user, address_row, _variant_row, client = ready_checkout(credit_limit="100.00")
    group, token = open_checkout(client)

    client.post(
        reverse("checkout"),
        post_data(group, token, address_row, use_credit=True),
    )

    order = Order.objects.get()
    assert order.credit_applied == Decimal("100.00")
    assert order.amount_due_on_delivery == Decimal("130.00")
    assert CreditAccount.objects.get(user=user).balance == Decimal("100.00")


def test_checkout_post_with_cod_for_a_credit_customer_writes_no_entry() -> None:
    user, address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")
    group, token = open_checkout(client)

    client.post(
        reverse("checkout"),
        post_data(group, token, address_row),
    )

    assert Order.objects.get().payment_method == Order.PaymentMethod.COD
    assert CreditEntry.objects.count() == 0
    assert CreditAccount.objects.get(user=user).balance == Decimal("0.00")


def test_checkout_post_twice_with_credit_charges_once() -> None:
    user, address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")
    group, token = open_checkout(client)
    data = post_data(group, token, address_row, use_credit=True)

    first = client.post(reverse("checkout"), data)
    second = client.post(reverse("checkout"), data)

    assert first.status_code == 302
    assert second.status_code == 302
    assert first.url == second.url
    order = Order.objects.get()
    assert CreditEntry.objects.filter(order=order).count() == 1
    assert CreditAccount.objects.get(user=user).balance == Decimal("230.00")


def test_confirmation_for_a_credit_order_says_added_to_khata_and_nothing_to_pay() -> None:
    user, address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")
    order = place_credit(user, address_row).order

    html = client.get(reverse("order_confirmation", args=[order.checkout_group])).content.decode()

    assert element_text(html, "data-payment-method") == payment_method_label(
        Order.PaymentMethod.CREDIT
    )
    assert element_text(html, "data-credit-sentence") == credit_confirmation_sentence(
        Decimal("230.00")
    )
    assert not has_element(html, "data-amount-due")


def test_confirmation_for_a_split_order_shows_both_parts() -> None:
    user, address_row, _variant_row, client = ready_checkout(credit_limit="100.00")
    order = place_credit(user, address_row).order

    html = client.get(reverse("order_confirmation", args=[order.checkout_group])).content.decode()

    assert element_text(html, "data-credit-sentence") == credit_confirmation_sentence(
        Decimal("100.00")
    )
    assert element_text(html, "data-amount-due") == cod_on_delivery_sentence(Decimal("130.00"))


def test_confirmation_for_a_cod_order_still_says_pay_cash() -> None:
    user, address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")
    order = place_cod(user, address_row).order

    html = client.get(reverse("order_confirmation", args=[order.checkout_group])).content.decode()

    assert not has_element(html, "data-payment-method")
    assert element_text(html, "data-amount-due") == cod_on_delivery_sentence(Decimal("230.00"))


def test_checkout_page_credit_exactly_at_the_available_credit_covers_everything() -> None:
    _user, _address_row, _variant_row, client = ready_checkout(credit_limit="230.00")
    set_settings(cod_cap=Decimal("100.00"))

    html = _page(client)

    assert _credit_box(html)["checked"] is True
    assert not has_element(html, "data-cash-due")
    assert has_element(html, "data-place-order")


def test_checkout_page_credit_one_paisa_short_leaves_a_cash_part_above_the_cap() -> None:
    _user, _address_row, _variant_row, client = ready_checkout(credit_limit="229.99")
    set_settings(cod_cap=Decimal("0.00"))

    html = _page(client)

    assert _credit_box(html)["checked"] is True
    assert element_text(html, "data-cash-due") == "₹0.01"
    assert has_element(html, "data-cod-limit")
    assert not has_element(html, "data-place-order")


def test_checkout_page_cod_exactly_at_the_cod_cap_is_accepted_without_credit() -> None:
    _user, address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")
    set_settings(cod_cap=Decimal("230.00"))
    group, token = open_checkout(client)

    client.post(reverse("checkout"), post_data(group, token, address_row))

    order = Order.objects.get()
    assert order.payment_method == Order.PaymentMethod.COD
    assert order.amount_due_on_delivery == Decimal("230.00")
