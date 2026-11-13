"""Tests for the coupon box on the checkout page (D83).

The box only previews a code: these tests read the page and the session, and
the order itself is placed in ``test_checkout_coupon_place.py``.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Address
from apps.cart.services import set_item_quantity
from apps.cart.stores import DbCartStore
from apps.orders.models import Order
from apps.orders.presentation import cod_sentence
from apps.orders.tests.checkout_helpers import (
    address,
    clear_settings,
    element_text,
    fill_cart,
    has_element,
    inputs,
    open_checkout,
    post_data,
    ready_checkout,
    reset_settings,
    set_settings,
    variant,
)
from apps.orders.tests.factories import OrderFactory
from apps.promotions.models import Coupon
from apps.promotions.rules import MESSAGES, CouponRefusal
from apps.promotions.tests.helpers import make_coupon, make_redemption
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before every test."""
    reset_settings()
    yield
    clear_settings()


def _page(client: Client) -> str:
    """The checkout page HTML for this logged-in client."""
    return client.get(reverse("checkout")).content.decode()


def _post(
    client: Client,
    group: str,
    token: str,
    address_row: Address,
    **extra: str,
):
    """Post the checkout form with these extra fields."""
    data = post_data(group, token, address_row)
    data.update(extra)
    return client.post(reverse("checkout"), data)


def _apply(client: Client, group: str, token: str, address_row: Address, code: str = "DIWALI10"):
    """Press Apply with this code."""
    return _post(client, group, token, address_row, coupon_code=code, coupon_action="apply")


def _refusal(reason: str, **params: object) -> str:
    """The one customer sentence of a refusal reason."""
    template = str(MESSAGES[reason])
    return template % params if params else template


def _textarea_text(html: str) -> str:
    """The text inside the customer-note box."""
    match = re.search(
        r'<textarea[^>]*name="customer_note"[^>]*>(.*?)</textarea>',
        html,
        re.DOTALL,
    )
    assert match is not None, "No note box on the page."
    return match.group(1)


def _checked_value(html: str, name: str) -> str:
    """The value of the one checked input with this name."""
    checked = [row for row in inputs(html, name) if row["checked"]]
    assert len(checked) == 1, f"Expected one checked {name}."
    return str(checked[0]["value"])


# --- the box itself -----------------------------------------------------------


def test_checkout_page_shows_the_coupon_box() -> None:
    _user, _address_row, _row, client = ready_checkout()

    html = _page(client)

    assert "Have a coupon?" in html
    assert 'name="coupon_code"' in html
    assert 'name="coupon_action" value="apply"' in html
    assert 'name="coupon_action" value="remove"' in html


def test_the_coupon_box_has_a_visible_label_and_a_named_input() -> None:
    _user, _address_row, _row, client = ready_checkout()

    html = _page(client)

    assert '<label for="coupon-code"' in html
    assert "Coupon code" in html
    assert 'id="coupon-code"' in html


# --- apply --------------------------------------------------------------------


def test_apply_a_percent_code_stores_it() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)

    _apply(client, group, token, address_row)

    assert client.session["checkout_coupon"] == "DIWALI10"


def test_apply_a_percent_code_shows_the_green_message() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row).content.decode()

    assert element_text(html, "data-coupon-message") == "Coupon DIWALI10 applied."


def test_apply_a_percent_code_shows_the_coupon_row_and_the_new_total() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row).content.decode()

    assert "Coupon (DIWALI10)" in html
    assert element_text(html, "data-item-total") == "₹200.00"
    assert element_text(html, "data-coupon-discount") == "-₹20.00"
    assert element_text(html, "data-grand-total") == "₹210.00"


def test_apply_a_fixed_code_shows_its_item_discount() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon(code="FLAT50", kind=Coupon.Kind.FIXED, amount_off=Decimal("50.00"))
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row, code="FLAT50").content.decode()

    assert element_text(html, "data-coupon-discount") == "-₹50.00"
    assert element_text(html, "data-grand-total") == "₹180.00"


def test_apply_a_free_delivery_code_discounts_the_delivery_fee() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon(code="FREESHIP", kind=Coupon.Kind.FREE_DELIVERY)
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row, code="FREESHIP").content.decode()

    assert element_text(html, "data-delivery-discount") == "-₹30.00"
    assert element_text(html, "data-grand-total") == "₹200.00"
    assert not has_element(html, "data-coupon-discount")


def test_apply_a_free_delivery_code_keeps_the_fee_before_the_coupon() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon(code="FREESHIP", kind=Coupon.Kind.FREE_DELIVERY)
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row, code="FREESHIP").content.decode()

    # The fee line still shows the cart's 30.00; the discount is its own row.
    assert element_text(html, "data-delivery-fee") == "₹30.00"


def test_apply_shows_what_the_code_saves() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row).content.decode()

    assert element_text(html, "data-coupon-applied") == ("DIWALI10 is applied. You save ₹20.00.")


def test_the_coupon_row_sits_between_the_item_total_and_the_delivery_line() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row).content.decode()

    assert html.index("data-item-total") < html.index("data-coupon-discount")
    assert html.index("data-coupon-discount") < html.index("data-delivery-fee")


def test_apply_does_not_place_an_order() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)

    response = _apply(client, group, token, address_row)

    assert response.status_code == 200
    assert Order.objects.count() == 0


def test_apply_keeps_the_checkout_group() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)

    _apply(client, group, token, address_row)

    assert client.session["checkout_group"] == group


# --- the posted choices are kept ---------------------------------------------


def test_apply_keeps_the_posted_address() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row).content.decode()

    assert _checked_value(html, "address") == str(address_row.pk)


def test_apply_keeps_the_posted_substitution() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)

    html = _post(
        client,
        group,
        token,
        address_row,
        coupon_code="DIWALI10",
        coupon_action="apply",
        substitution_preference=Order.SubstitutionPreference.REFUND,
    ).content.decode()

    assert _checked_value(html, "substitution_preference") == Order.SubstitutionPreference.REFUND


def test_apply_keeps_the_posted_note() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)

    html = _post(
        client,
        group,
        token,
        address_row,
        coupon_code="DIWALI10",
        coupon_action="apply",
        customer_note="Ring the bell twice",
    ).content.decode()

    assert _textarea_text(html) == "Ring the bell twice"


def test_apply_keeps_the_posted_credit_tick() -> None:
    _user, address_row, _row, client = ready_checkout(credit_limit="1000.00")
    make_coupon()
    group, token = open_checkout(client)

    html = _post(
        client,
        group,
        token,
        address_row,
        coupon_code="DIWALI10",
        coupon_action="apply",
        use_credit="on",
    ).content.decode()

    assert inputs(html, "use_credit")[0]["checked"] is True


def test_apply_keeps_the_posted_online_choice(settings) -> None:
    settings.PAYMENT_PROVIDER = "fake"
    _user, address_row, _row, client = ready_checkout(credit_limit="100.00")
    make_coupon()
    group, token = open_checkout(client)

    html = _post(
        client,
        group,
        token,
        address_row,
        coupon_code="DIWALI10",
        coupon_action="apply",
        use_credit="on",
        remainder_method=Order.PaymentMethod.ONLINE,
    ).content.decode()

    assert _checked_value(html, "remainder_method") == Order.PaymentMethod.ONLINE


def test_an_apply_with_a_stale_group_is_refused() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    _group, token = open_checkout(client)

    # The coupon step runs after the group checks, so a stale group is refused
    # exactly like any other post and stores no code.
    response = _post(
        client,
        group=str(uuid4()),
        token=token,
        address_row=address_row,
        coupon_code="DIWALI10",
        coupon_action="apply",
    )

    assert response.status_code == 302
    assert "checkout_coupon" not in client.session


def test_apply_shows_no_field_errors() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)

    # The address is blank, so a bound form would show its error here.
    html = _post(
        client,
        group,
        token,
        address_row,
        address="",
        coupon_code="DIWALI10",
        coupon_action="apply",
    ).content.decode()

    assert not has_element(html, "data-form-errors")
    assert client.session["checkout_coupon"] == "DIWALI10"


# --- refusals -----------------------------------------------------------------


def test_apply_with_an_empty_box_asks_for_a_code() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row, code="").content.decode()

    assert element_text(html, "data-checkout-error") == "Please type a coupon code."
    assert "checkout_coupon" not in client.session
    assert Order.objects.count() == 0


def test_apply_with_spaces_and_lower_case_stores_the_canonical_code() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row, code="  diwali10 ").content.decode()

    assert client.session["checkout_coupon"] == "DIWALI10"
    assert element_text(html, "data-coupon-message") == "Coupon DIWALI10 applied."


def test_apply_an_unknown_code_is_refused() -> None:
    _user, address_row, _row, client = ready_checkout()
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row, code="NOPE").content.decode()

    assert element_text(html, "data-checkout-error") == _refusal(CouponRefusal.NOT_FOUND)
    assert "checkout_coupon" not in client.session
    # A refusal must never also say the code was applied.
    assert not has_element(html, "data-coupon-message")


def test_apply_a_switched_off_code_is_refused() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon(is_active=False)
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row).content.decode()

    assert element_text(html, "data-checkout-error") == _refusal(CouponRefusal.INACTIVE)
    assert "checkout_coupon" not in client.session


def test_apply_an_expired_code_is_refused() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon(ends_at=timezone.now() - timedelta(minutes=1))
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row).content.decode()

    assert element_text(html, "data-checkout-error") == _refusal(CouponRefusal.EXPIRED)
    assert "checkout_coupon" not in client.session


def test_apply_a_code_that_has_not_started_is_refused() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon(starts_at=timezone.now() + timedelta(days=1))
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row).content.decode()

    assert element_text(html, "data-checkout-error") == _refusal(CouponRefusal.NOT_STARTED)
    assert "checkout_coupon" not in client.session


def test_apply_below_the_minimum_shows_the_amount_short() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon(min_order_value=Decimal("500.00"))
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row).content.decode()

    assert element_text(html, "data-checkout-error") == _refusal(
        CouponRefusal.BELOW_MINIMUM, amount="₹300.00"
    )
    assert "checkout_coupon" not in client.session


def test_apply_a_first_order_code_for_a_returning_customer_is_refused() -> None:
    user, address_row, _row, client = ready_checkout()
    OrderFactory(user=user)
    make_coupon(first_order_only=True)
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row).content.decode()

    assert element_text(html, "data-checkout-error") == _refusal(CouponRefusal.NOT_FIRST_ORDER)
    assert "checkout_coupon" not in client.session


def test_apply_a_fully_used_code_is_refused() -> None:
    _user, address_row, _row, client = ready_checkout()
    coupon = make_coupon(total_limit=1)
    make_redemption(coupon)
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row).content.decode()

    assert element_text(html, "data-checkout-error") == _refusal(CouponRefusal.TOTAL_LIMIT_REACHED)
    assert "checkout_coupon" not in client.session


def test_apply_a_code_the_customer_already_used_is_refused() -> None:
    user, address_row, _row, client = ready_checkout()
    coupon = make_coupon(per_customer_limit=1)
    make_redemption(coupon, user=user)
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row).content.decode()

    assert element_text(html, "data-checkout-error") == _refusal(
        CouponRefusal.PER_CUSTOMER_LIMIT_REACHED
    )
    assert "checkout_coupon" not in client.session


def test_apply_a_free_delivery_code_on_a_free_delivery_cart_is_refused() -> None:
    user, address_row, row, client = ready_checkout()
    # Five packets reach the shop's 500.00 free-delivery threshold.
    set_item_quantity(DbCartStore(user), row, Decimal("5"))
    make_coupon(code="FREESHIP", kind=Coupon.Kind.FREE_DELIVERY)
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row, code="FREESHIP").content.decode()

    assert element_text(html, "data-checkout-error") == _refusal(CouponRefusal.NO_BENEFIT)
    assert "checkout_coupon" not in client.session


def test_a_refused_code_drops_the_code_applied_before() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)

    html = _apply(client, group, token, address_row, code="NOPE").content.decode()

    assert "checkout_coupon" not in client.session
    assert element_text(html, "data-checkout-error") == _refusal(CouponRefusal.NOT_FOUND)
    assert element_text(html, "data-grand-total") == "₹230.00"
    assert not has_element(html, "data-coupon-discount")


# --- remove -------------------------------------------------------------------


def test_remove_drops_the_code_and_says_so() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)

    html = _post(client, group, token, address_row, coupon_action="remove").content.decode()

    assert "checkout_coupon" not in client.session
    assert element_text(html, "data-coupon-message") == "Coupon removed."
    assert element_text(html, "data-grand-total") == "₹230.00"


def test_remove_without_a_code_still_says_removed() -> None:
    _user, address_row, _row, client = ready_checkout()
    group, token = open_checkout(client)

    html = _post(client, group, token, address_row, coupon_action="remove").content.decode()

    assert "checkout_coupon" not in client.session
    assert element_text(html, "data-coupon-message") == "Coupon removed."
    assert Order.objects.count() == 0


# --- every total uses the discounted amount ----------------------------------


def test_the_credit_part_uses_the_discounted_total() -> None:
    _user, address_row, _row, client = ready_checkout(credit_limit="250.00")
    make_coupon()
    group, token = open_checkout(client)

    html = _post(
        client,
        group,
        token,
        address_row,
        coupon_code="DIWALI10",
        coupon_action="apply",
        use_credit="on",
    ).content.decode()

    assert element_text(html, "data-credit-applied") == "₹210.00"
    assert not has_element(html, "data-cash-due")


def test_the_cash_cap_check_uses_the_discounted_total() -> None:
    _user, address_row, _row, client = ready_checkout()
    set_settings(cod_cap=Decimal("220.00"))
    make_coupon()
    group, token = open_checkout(client)
    assert not has_element(_page(client), "data-place-order")

    html = _apply(client, group, token, address_row).content.decode()

    assert not has_element(html, "data-cod-limit")
    assert has_element(html, "data-place-order")


def test_an_order_over_the_cash_cap_is_refused_until_a_coupon_fits() -> None:
    _user, address_row, _row, client = ready_checkout()
    set_settings(cod_cap=Decimal("200.00"))
    make_coupon()
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row).content.decode()

    # 210.00 is still above the 200.00 cap, so the button stays away.
    assert has_element(html, "data-cod-limit")
    assert not has_element(html, "data-place-order")


def test_the_cash_sentence_uses_the_discounted_total() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)

    html = _apply(client, group, token, address_row).content.decode()

    assert element_text(html, "data-cod-sentence") == cod_sentence(Decimal("210.00"))


def test_the_online_amount_uses_the_discounted_total(settings) -> None:
    settings.PAYMENT_PROVIDER = "fake"
    _user, address_row, _row, client = ready_checkout(credit_limit="100.00")
    make_coupon()
    group, token = open_checkout(client)

    html = _post(
        client,
        group,
        token,
        address_row,
        coupon_code="DIWALI10",
        coupon_action="apply",
        use_credit="on",
        remainder_method=Order.PaymentMethod.ONLINE,
    ).content.decode()

    assert element_text(html, "data-credit-applied") == "₹100.00"
    assert element_text(html, "data-online-amount") == "₹110.00"


# --- a stored code that stops working ----------------------------------------


def test_a_switched_off_stored_code_is_dropped_on_the_next_render() -> None:
    _user, address_row, _row, client = ready_checkout()
    coupon = make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)
    coupon.is_active = False
    coupon.save(update_fields=["is_active"])

    html = _page(client)

    assert "checkout_coupon" not in client.session
    assert element_text(html, "data-checkout-error") == _refusal(CouponRefusal.INACTIVE)
    assert element_text(html, "data-grand-total") == "₹230.00"
    assert not has_element(html, "data-coupon-discount")


def test_an_expired_stored_code_is_dropped_on_the_next_render() -> None:
    _user, address_row, _row, client = ready_checkout()
    coupon = make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)
    coupon.ends_at = timezone.now() - timedelta(minutes=1)
    coupon.save(update_fields=["ends_at"])

    html = _page(client)

    assert "checkout_coupon" not in client.session
    assert element_text(html, "data-checkout-error") == _refusal(CouponRefusal.EXPIRED)
    # The sentence is shown once: the next render has no code to judge.
    assert str(MESSAGES[CouponRefusal.EXPIRED]) not in _page(client)


def test_a_stored_code_below_the_minimum_is_dropped_after_the_cart_changed() -> None:
    user, address_row, row, client = ready_checkout()
    # Three packets are 300.00 of goods, the coupon's minimum; two are below it
    # but still above the shop's own 200.00 minimum, so checkout still opens.
    set_item_quantity(DbCartStore(user), row, Decimal("3"))
    make_coupon(min_order_value=Decimal("300.00"))
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)
    set_item_quantity(DbCartStore(user), row, Decimal("2"))

    html = _page(client)

    assert "checkout_coupon" not in client.session
    assert element_text(html, "data-checkout-error") == _refusal(
        CouponRefusal.BELOW_MINIMUM, amount="₹100.00"
    )
    assert element_text(html, "data-grand-total") == "₹230.00"


# --- people and sessions ------------------------------------------------------


def test_a_guest_is_sent_to_log_in() -> None:
    response = Client().get(reverse("checkout"))

    assert response.status_code == 302
    assert response.url.startswith(f"{reverse('account_login')}?next=")
    assert "next=/checkout/" in response.url


def test_a_second_customer_has_no_stored_code() -> None:
    _user, address_row, _row, client = ready_checkout()
    make_coupon()
    group, token = open_checkout(client)
    _apply(client, group, token, address_row)

    other = UserFactory()
    address(other)
    fill_cart(other, variant())
    other_client = Client()
    other_client.force_login(other)
    html = _page(other_client)

    assert "checkout_coupon" not in other_client.session
    assert not has_element(html, "data-coupon-discount")
    assert not has_element(html, "data-coupon-applied")
    assert element_text(html, "data-grand-total") == "₹230.00"
