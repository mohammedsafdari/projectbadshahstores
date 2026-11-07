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
    PAYMENT_METHOD_INVALID,
    available_credit_sentence,
    cod_on_delivery_sentence,
    cod_sentence,
    credit_checkout_sentence,
    credit_confirmation_sentence,
    credit_limit_message,
    credit_refused_message,
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


def _payment_radio(html: str, value: str) -> dict[str, object]:
    """The one payment radio whose value is ``value``."""
    radios = {str(row["value"]): row for row in inputs(html, "payment_method")}
    assert value in radios, f"No payment radio for {value}."
    return radios[value]


def test_checkout_page_offers_no_credit_to_a_customer_without_an_account() -> None:
    _user, _address_row, _variant_row, client = ready_checkout()

    html = _page(client)

    assert inputs(html, "payment_method") == []
    assert not has_element(html, "data-available-credit")
    assert not has_element(html, "data-credit-sentence")
    assert element_text(html, "data-cod-sentence") == cod_sentence(Decimal("230.00"))
    assert has_element(html, "data-place-order")


def test_checkout_page_offers_no_credit_to_a_suspended_account() -> None:
    _user, _address_row, _variant_row, client = ready_checkout(
        credit_limit="1000.00", credit_active=False
    )

    html = _page(client)

    assert inputs(html, "payment_method") == []
    assert not has_element(html, "data-available-credit")
    assert has_element(html, "data-place-order")


def test_checkout_page_offers_credit_with_the_available_amount() -> None:
    _user, _address_row, _variant_row, client = ready_checkout(
        credit_limit="1000.00", credit_balance="100.00"
    )

    html = _page(client)

    radios = inputs(html, "payment_method")
    assert {str(row["value"]) for row in radios} == {"COD", "CREDIT"}
    assert element_text(html, "data-available-credit") == available_credit_sentence(
        Decimal("900.00")
    )


def test_checkout_page_selects_cod_by_default() -> None:
    _user, _address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")

    html = _page(client)

    assert _payment_radio(html, "COD")["checked"] is True
    assert _payment_radio(html, "CREDIT")["checked"] is False


def test_checkout_page_disables_credit_when_the_order_is_above_the_available_credit() -> None:
    _user, _address_row, _variant_row, client = ready_checkout(credit_limit="100.00")

    html = _page(client)

    credit = _payment_radio(html, "CREDIT")
    assert credit["disabled"] is True
    assert credit["checked"] is False
    assert _payment_radio(html, "COD")["checked"] is True
    assert element_text(html, "data-credit-limit") == credit_limit_message(Decimal("100.00"))
    assert has_element(html, "data-place-order")


def test_checkout_page_above_the_cod_cap_selects_credit_when_it_fits() -> None:
    _user, _address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")
    set_settings(cod_cap=Decimal("100.00"))

    html = _page(client)

    assert _payment_radio(html, "COD")["disabled"] is True
    assert _payment_radio(html, "COD")["checked"] is False
    assert _payment_radio(html, "CREDIT")["checked"] is True
    assert has_element(html, "data-place-order")


def test_checkout_page_above_the_cod_cap_and_credit_does_not_fit_cannot_be_submitted() -> None:
    _user, _address_row, _variant_row, client = ready_checkout(credit_limit="100.00")
    set_settings(cod_cap=Decimal("100.00"))

    html = _page(client)

    assert _payment_radio(html, "COD")["disabled"] is True
    assert _payment_radio(html, "CREDIT")["disabled"] is True
    assert has_element(html, "data-cod-limit")
    assert has_element(html, "data-credit-limit")
    assert not has_element(html, "data-place-order")


def test_checkout_page_shows_the_khata_sentence_for_credit() -> None:
    _user, _address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")

    html = _page(client)

    assert element_text(html, "data-credit-sentence") == credit_checkout_sentence(Decimal("230.00"))


def test_checkout_post_with_credit_places_a_credit_order() -> None:
    _user, address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")
    group, token = open_checkout(client)

    response = client.post(
        reverse("checkout"),
        post_data(group, token, address_row, payment_method=Order.PaymentMethod.CREDIT),
    )

    order = Order.objects.get()
    assert response.status_code == 302
    assert response.url == reverse("order_confirmation", args=[order.checkout_group])
    assert order.status == Order.Status.CONFIRMED
    assert order.payment_method == Order.PaymentMethod.CREDIT


def test_checkout_post_with_credit_adds_to_the_balance() -> None:
    user, address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")
    group, token = open_checkout(client)

    client.post(
        reverse("checkout"),
        post_data(group, token, address_row, payment_method=Order.PaymentMethod.CREDIT),
    )

    account = CreditAccount.objects.get(user=user)
    assert account.balance == Decimal("230.00")
    assert CreditEntry.objects.get(order__user=user).amount == Decimal("230.00")


def test_checkout_post_with_credit_above_the_limit_shows_the_error_and_creates_nothing() -> None:
    user, address_row, _variant_row, client = ready_checkout(credit_limit="100.00")
    group, token = open_checkout(client)

    response = client.post(
        reverse("checkout"),
        post_data(group, token, address_row, payment_method=Order.PaymentMethod.CREDIT),
    )

    html = response.content.decode()
    assert response.status_code == 200
    assert element_text(html, "data-form-errors") == credit_refused_message(Decimal("100.00"))
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
        post_data(group, token, address_row, payment_method=Order.PaymentMethod.CREDIT),
    )

    assert response.status_code == 200
    assert element_text(response.content.decode(), "data-form-errors") == str(
        CREDIT_UNAVAILABLE_MESSAGE
    )
    assert Order.objects.count() == 0
    assert CartItem.objects.filter(cart__user=user).count() == 1


def test_checkout_post_with_an_unknown_payment_method_shows_a_form_error() -> None:
    _user, address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")
    group, token = open_checkout(client)

    response = client.post(
        reverse("checkout"),
        post_data(group, token, address_row, payment_method="ONLINE"),
    )

    assert response.status_code == 200
    assert element_text(response.content.decode(), "data-form-errors") == str(
        PAYMENT_METHOD_INVALID
    )
    assert Order.objects.count() == 0


def test_checkout_post_defaults_to_cod_when_no_method_is_sent() -> None:
    _user, address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")
    group, token = open_checkout(client)

    client.post(reverse("checkout"), post_data(group, token, address_row))

    assert Order.objects.get().payment_method == Order.PaymentMethod.COD


def test_checkout_post_with_cod_for_a_credit_customer_writes_no_entry() -> None:
    user, address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")
    group, token = open_checkout(client)

    client.post(
        reverse("checkout"),
        post_data(group, token, address_row, payment_method=Order.PaymentMethod.COD),
    )

    assert Order.objects.get().payment_method == Order.PaymentMethod.COD
    assert CreditEntry.objects.count() == 0
    assert CreditAccount.objects.get(user=user).balance == Decimal("0.00")


def test_checkout_post_twice_with_credit_charges_once() -> None:
    user, address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")
    group, token = open_checkout(client)
    data = post_data(group, token, address_row, payment_method=Order.PaymentMethod.CREDIT)

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
    assert element_text(html, "data-amount-due") == credit_confirmation_sentence(Decimal("230.00"))


def test_confirmation_for_a_cod_order_still_says_pay_cash() -> None:
    user, address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")
    order = place_cod(user, address_row).order

    html = client.get(reverse("order_confirmation", args=[order.checkout_group])).content.decode()

    assert not has_element(html, "data-payment-method")
    assert element_text(html, "data-amount-due") == cod_on_delivery_sentence(Decimal("230.00"))


def test_checkout_page_credit_exactly_at_the_available_credit_is_selectable() -> None:
    _user, _address_row, _variant_row, client = ready_checkout(credit_limit="230.00")
    set_settings(cod_cap=Decimal("100.00"))

    html = _page(client)

    credit = _payment_radio(html, "CREDIT")
    assert credit["disabled"] is False
    assert credit["checked"] is True
    assert has_element(html, "data-place-order")


def test_checkout_page_credit_one_paisa_above_the_available_credit_is_disabled() -> None:
    _user, _address_row, _variant_row, client = ready_checkout(credit_limit="229.99")
    set_settings(cod_cap=Decimal("100.00"))

    html = _page(client)

    assert _payment_radio(html, "CREDIT")["disabled"] is True
    assert _payment_radio(html, "COD")["disabled"] is True
    assert not has_element(html, "data-place-order")


def test_checkout_page_cod_exactly_at_the_cod_cap_is_selectable_for_a_credit_customer() -> None:
    _user, _address_row, _variant_row, client = ready_checkout(credit_limit="1000.00")
    set_settings(cod_cap=Decimal("230.00"))

    html = _page(client)

    cod = _payment_radio(html, "COD")
    assert cod["disabled"] is False
    assert cod["checked"] is True
