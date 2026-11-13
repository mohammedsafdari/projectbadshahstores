"""Tests for placing an order with the coupon from the checkout box (D83).

The money rules themselves are Slice 44b-1 tests; these tests prove the page
passes the code through and that the page's preview is never the authority.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Address
from apps.cart.services import remove_item, set_item_quantity
from apps.cart.stores import DbCartStore
from apps.orders.models import Order
from apps.orders.presentation import cod_on_delivery_sentence
from apps.orders.tests.checkout_helpers import (
    clear_settings,
    element_text,
    has_element,
    inputs,
    open_checkout,
    post_data,
    ready_checkout,
    reset_settings,
)
from apps.promotions.models import Coupon, CouponRedemption
from apps.promotions.rules import MESSAGES, CouponRefusal
from apps.promotions.tests.helpers import make_coupon

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before every test."""
    reset_settings()
    yield
    clear_settings()


def _post(client: Client, group: str, token: str, address_row: Address, **extra: str):
    """Post the checkout form with these extra fields."""
    data = post_data(group, token, address_row)
    data.update(extra)
    return client.post(reverse("checkout"), data)


def _apply(client: Client, group: str, token: str, address_row: Address, code: str = "DIWALI10"):
    """Press Apply with this code."""
    return _post(client, group, token, address_row, coupon_code=code, coupon_action="apply")


def _refusal(reason: str) -> str:
    """The one customer sentence of a refusal reason."""
    return str(MESSAGES[reason])


def _confirmation_html(client: Client, response) -> str:
    """Follow the successful POST to its confirmation page and read it."""
    assert response.status_code == 302
    return client.get(response.url).content.decode()


# --- placing with a stored code ----------------------------------------------


def test_placing_with_a_stored_code_freezes_the_discounts() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)

    _post(client, group, token, address_row, coupon_code="DIWALI10")

    order = Order.objects.get()
    assert order.item_total == Decimal("200.00")
    assert order.discount_total == Decimal("20.00")
    assert order.delivery_discount == Decimal("0.00")
    assert order.grand_total == Decimal("210.00")
    assert order.coupon_code == "DIWALI10"


def test_placing_with_a_stored_code_sets_the_cash_part() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)

    _post(client, group, token, address_row, coupon_code="DIWALI10")

    order = Order.objects.get()
    assert order.amount_due_on_delivery == Decimal("210.00")


def test_placing_with_a_stored_code_writes_one_redemption() -> None:
    _user, address_row, _row, client = ready_checkout()
    coupon = make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)

    _post(client, group, token, address_row, coupon_code="DIWALI10")

    redemption = CouponRedemption.objects.get(order=Order.objects.get())
    assert redemption.coupon_id == coupon.pk
    assert redemption.discount_amount == Decimal("20.00")
    assert redemption.delivery_discount_amount == Decimal("0.00")


def test_the_code_is_gone_from_the_session_after_the_order() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)

    _post(client, group, token, address_row, coupon_code="DIWALI10")

    assert "checkout_coupon" not in client.session


def test_the_checkout_group_is_gone_after_the_order() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)

    _post(client, group, token, address_row, coupon_code="DIWALI10")

    assert "checkout_group" not in client.session


def test_the_confirmation_page_shows_the_discount() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)

    html = _confirmation_html(client, _post(client, group, token, address_row))

    assert "Coupon (DIWALI10)" in html
    assert element_text(html, "data-coupon-discount") == "-₹20.00"
    assert element_text(html, "data-grand-total") == "₹210.00"


# --- a refusal at order time --------------------------------------------------


def test_a_coupon_switched_off_before_the_place_is_refused_at_order_time() -> None:
    _user, address_row, _row, client = ready_checkout()
    coupon = make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)
    coupon.is_active = False
    coupon.save(update_fields=["is_active"])

    response = _post(client, group, token, address_row, coupon_code="DIWALI10")
    html = response.content.decode()

    assert response.status_code == 200
    assert element_text(html, "data-checkout-error") == _refusal(CouponRefusal.INACTIVE)
    assert Order.objects.count() == 0
    assert CouponRedemption.objects.count() == 0


def test_the_refused_code_is_dropped_from_the_session() -> None:
    _user, address_row, _row, client = ready_checkout()
    coupon = make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)
    coupon.is_active = False
    coupon.save(update_fields=["is_active"])

    html = _post(client, group, token, address_row, coupon_code="DIWALI10").content.decode()

    assert "checkout_coupon" not in client.session
    assert element_text(html, "data-grand-total") == "₹230.00"


def test_the_per_customer_limit_refuses_a_second_order() -> None:
    user, address_row, row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)
    _post(client, group, token, address_row, coupon_code="DIWALI10")

    # The order cleared the cart, so put the goods back and open a fresh
    # checkout: the one allowed use of this code is spent.
    set_item_quantity(DbCartStore(user), row, Decimal("2"))
    second_group, second_token = open_checkout(client)
    html = _apply(client, second_group, second_token, address_row).content.decode()

    assert element_text(html, "data-checkout-error") == _refusal(
        CouponRefusal.PER_CUSTOMER_LIMIT_REACHED
    )
    assert Order.objects.count() == 1


def test_a_replay_of_the_same_group_places_one_order() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)

    first = _post(client, group, token, address_row, coupon_code="DIWALI10")
    second = _post(client, group, token, address_row, coupon_code="DIWALI10")

    assert first.status_code == 302
    assert second.status_code == 302
    assert second.url == first.url
    assert Order.objects.count() == 1
    assert CouponRedemption.objects.count() == 1


def test_a_replay_does_not_judge_the_code_again() -> None:
    _user, address_row, _row, client = ready_checkout()
    coupon = make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)
    _post(client, group, token, address_row, coupon_code="DIWALI10")
    coupon.is_active = False
    coupon.save(update_fields=["is_active"])

    response = _post(client, group, token, address_row, coupon_code="DIWALI10")

    assert response.status_code == 302
    assert Order.objects.count() == 1


# --- free delivery and a zero total -----------------------------------------


def test_a_free_delivery_order_has_no_fee_on_the_confirmation_page() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon(code="FREESHIP", kind=Coupon.Kind.FREE_DELIVERY)
    group, token = open_checkout(client)
    _apply(client, group, token, address_row, code="FREESHIP")

    html = _confirmation_html(client, _post(client, group, token, address_row))

    # The fee line keeps the fee before the coupon (D83); the delivery discount
    # row takes it off, so the total and the cash due hold no fee at all.
    assert element_text(html, "data-delivery-fee") == "₹30.00"
    assert element_text(html, "data-delivery-discount") == "-₹30.00"
    assert element_text(html, "data-grand-total") == "₹200.00"
    assert element_text(html, "data-amount-due") == cod_on_delivery_sentence(Decimal("200.00"))


def test_a_zero_total_order_is_placed_and_the_confirmation_shows_zero() -> None:
    user, address_row, row, client = ready_checkout()
    # Five packets are 500.00, so delivery is already free and a 500.00 fixed
    # coupon leaves nothing to pay.
    set_item_quantity(DbCartStore(user), row, Decimal("5"))
    make_coupon(code="MEGA500", kind=Coupon.Kind.FIXED, amount_off=Decimal("500.00"))
    group, token = open_checkout(client)
    _apply(client, group, token, address_row, code="MEGA500")

    html = _confirmation_html(client, _post(client, group, token, address_row))

    assert Order.objects.get().grand_total == Decimal("0.00")
    assert element_text(html, "data-grand-total") == "₹0.00"
    assert not has_element(html, "data-amount-due")


# --- the typed code must never be left behind --------------------------------


def test_a_typed_new_code_without_coupon_action_applies_it_and_places_nothing() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)

    response = _post(client, group, token, address_row, coupon_code="diwali10")
    html = response.content.decode()

    assert response.status_code == 200
    assert Order.objects.count() == 0
    assert element_text(html, "data-coupon-message") == "Coupon DIWALI10 applied."


def test_a_typed_new_code_without_coupon_action_is_stored() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)

    _post(client, group, token, address_row, coupon_code="diwali10")

    assert client.session["checkout_coupon"] == "DIWALI10"


def test_a_typed_code_equal_to_the_applied_code_places_the_order() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)

    _post(client, group, token, address_row, coupon_code=" DIWALI10 ")

    assert Order.objects.count() == 1


def test_an_empty_box_places_the_order() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)

    _post(client, group, token, address_row, coupon_code="")

    assert Order.objects.count() == 1


def test_coupon_action_apply_alone_never_places_an_order() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)

    _post(client, group, token, address_row, coupon_code="DIWALI10", coupon_action="apply")

    assert Order.objects.count() == 0
    assert client.session["checkout_coupon"] == "DIWALI10"


def test_the_apply_button_is_the_first_submit_button_in_the_form() -> None:
    _user, _address_row, _row, client = ready_checkout()

    html = client.get(reverse("checkout")).content.decode()

    assert html.index('name="coupon_action" value="apply"') < html.index("data-place-order")
    assert len(inputs(html, "coupon_action")) == 0  # buttons, not inputs


# --- an order without a code is unchanged ------------------------------------


def test_a_cart_that_changed_keeps_the_code() -> None:
    user, address_row, row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)
    # An empty cart sends the customer back to the cart page; the code must
    # survive so their next try still has the discount.
    remove_item(DbCartStore(user), row)

    response = _post(client, group, token, address_row)

    assert response.status_code == 302
    assert response.url == reverse("cart_detail")
    assert client.session["checkout_coupon"] == "DIWALI10"


def test_an_order_with_no_code_is_exactly_as_before() -> None:
    _user, address_row, _row, client = ready_checkout()
    group, token = open_checkout(client)

    html = _confirmation_html(client, _post(client, group, token, address_row))

    order = Order.objects.get()
    assert order.coupon_code == ""
    assert order.discount_total == Decimal("0.00")
    assert order.delivery_discount == Decimal("0.00")
    assert order.grand_total == Decimal("230.00")
    assert not has_element(html, "data-coupon-discount")
    assert not has_element(html, "data-delivery-discount")


def test_a_credit_order_with_a_code_uses_the_discounted_total() -> None:
    _user, address_row, _row, client = ready_checkout(credit_limit="1000.00")
    make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)

    _post(client, group, token, address_row, use_credit="on")

    order = Order.objects.get()
    assert order.payment_method == Order.PaymentMethod.CREDIT
    assert order.credit_applied == Decimal("210.00")
    assert order.amount_due_on_delivery == Decimal("0.00")


def test_a_coded_order_keeps_the_posted_note_and_substitution() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)

    _post(
        client,
        group,
        token,
        address_row,
        customer_note="Ring the bell twice",
        substitution_preference=Order.SubstitutionPreference.REFUND,
    )

    order = Order.objects.get()
    assert order.customer_note == "Ring the bell twice"
    assert order.substitution_preference == Order.SubstitutionPreference.REFUND
