"""Checkout, confirmation and detail tests for online payment (D77)."""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.orders.models import Order
from apps.orders.tests.checkout_helpers import open_checkout, post_data
from apps.payments.models import Payment
from apps.payments.tests.factories import CapturedPaymentFactory, FailedPaymentFactory
from apps.payments.tests.helpers import (
    clear_settings,
    factory_online_order,
    online_order,
    ready_customer,
    reset_settings,
)
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _online_shop(settings):
    """A clean settings row and the fake provider for every test."""
    reset_settings()
    settings.PAYMENT_PROVIDER = "fake"
    yield
    clear_settings()


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def order_user():
    """A fresh customer for a factory order."""
    return UserFactory()


def test_checkout_shows_the_remainder_radios_when_online_is_configured() -> None:
    user, _address_row, _row = ready_customer()

    html = _client(user).get(reverse("checkout")).content.decode()

    assert 'name="remainder_method"' in html
    assert 'value="ONLINE"' in html
    assert "Pay online" in html


def test_checkout_hides_the_radios_when_no_gateway_is_configured(settings) -> None:
    settings.PAYMENT_PROVIDER = ""
    user, _address_row, _row = ready_customer()

    html = _client(user).get(reverse("checkout")).content.decode()

    assert 'value="ONLINE"' not in html
    assert 'type="hidden" name="remainder_method" value="COD"' in html


def test_checkout_hides_the_remainder_when_the_khata_covers_everything() -> None:
    user, address_row, _row = ready_customer(credit_limit="1000.00")
    client = _client(user)
    group, token = open_checkout(client)
    data = post_data(group, token, address_row, use_credit=True)
    # A missing address makes the form invalid, so the page re-renders with the
    # posted khata choice instead of placing the order.
    del data["address"]

    html = client.post(reverse("checkout"), data).content.decode()

    assert "remainder_method" not in html


def test_checkout_shows_the_online_sentence_for_a_partial_khata() -> None:
    user, address_row, _row = ready_customer(credit_limit="100.00")
    client = _client(user)
    group, token = open_checkout(client)
    data = post_data(group, token, address_row, use_credit=True)
    data["remainder_method"] = "ONLINE"
    # The invalid form re-renders with the khata and online choices, so the
    # 130.00 online part can be read from the HTML.
    del data["address"]

    html = client.post(reverse("checkout"), data).content.decode()

    assert "data-remainder-methods" in html
    assert "130.00" in html


def test_posting_online_places_a_payment_pending_order() -> None:
    user, address_row, _row = ready_customer()
    client = _client(user)
    group, token = open_checkout(client)
    data = post_data(group, token, address_row)
    data["remainder_method"] = "ONLINE"

    response = client.post(reverse("checkout"), data)

    assert response.status_code == 302
    order = Order.objects.get()
    assert order.status == Order.Status.PAYMENT_PENDING
    assert order.online_amount == Decimal("230.00")
    assert order.amount_due_on_delivery == Decimal("0.00")
    assert response.url == reverse("order_confirmation", args=[order.checkout_group])


def test_posting_online_without_a_gateway_is_refused(settings) -> None:
    settings.PAYMENT_PROVIDER = ""
    user, address_row, _row = ready_customer()
    client = _client(user)
    group, token = open_checkout(client)
    data = post_data(group, token, address_row)
    data["remainder_method"] = "ONLINE"

    response = client.post(reverse("checkout"), data)

    assert response.status_code == 200
    assert "Online payment is not available" in response.content.decode()
    assert Order.objects.count() == 0


def test_posting_cash_still_places_a_confirmed_order() -> None:
    user, address_row, _row = ready_customer()
    client = _client(user)
    group, token = open_checkout(client)

    response = client.post(reverse("checkout"), post_data(group, token, address_row))

    assert response.status_code == 302
    order = Order.objects.get()
    assert order.status == Order.Status.CONFIRMED
    assert order.payment_method == Order.PaymentMethod.COD


def test_posting_credit_and_online_records_both_parts() -> None:
    user, address_row, _row = ready_customer(credit_limit="100.00")
    client = _client(user)
    group, token = open_checkout(client)
    data = post_data(group, token, address_row, use_credit=True)
    data["remainder_method"] = "ONLINE"

    response = client.post(reverse("checkout"), data)

    assert response.status_code == 302
    order = Order.objects.get()
    assert order.payment_method == Order.PaymentMethod.CREDIT
    assert order.credit_applied == Decimal("100.00")
    assert order.online_amount == Decimal("130.00")
    assert order.amount_due_on_delivery == Decimal("0.00")


def test_the_confirmation_page_offers_the_pay_button_for_a_pending_order() -> None:
    _user, order, _row = online_order()
    client = _client(order.user)

    html = client.get(reverse("order_confirmation", args=[order.checkout_group])).content.decode()

    assert "data-pay-now" in html
    assert "Pay ₹230.00 online now." in html


def test_the_confirmation_page_offers_try_again_after_a_failure() -> None:
    order = factory_online_order(status=Order.Status.PAYMENT_FAILED)
    FailedPaymentFactory(order=order)
    client = _client(order.user)

    html = client.get(reverse("order_confirmation", args=[order.checkout_group])).content.decode()

    assert "data-pay-now" in html
    assert "Try again" in html
    assert "Update cart" in html


def test_the_detail_page_marks_a_paid_order_and_hides_the_cancel_link() -> None:
    user = order_user()
    order = factory_online_order(
        user=user,
        status=Order.Status.CONFIRMED,
        online_amount=Decimal("230.00"),
        amount_due_on_delivery=Decimal("0.00"),
    )
    CapturedPaymentFactory(order=order)
    client = _client(user)

    html = client.get(reverse("order_detail", args=[order.checkout_group])).content.decode()

    assert "data-paid-cannot-cancel" in html
    assert "data-cancel-link" not in html
    assert "Paid online" in html


def test_the_detail_page_offers_payment_for_a_pending_order() -> None:
    _user, order, _row = online_order()
    client = _client(order.user)

    html = client.get(reverse("order_detail", args=[order.checkout_group])).content.decode()

    assert "data-pay-now" in html
    assert "Pay online" in html


def test_the_detail_page_shows_the_failure_message_and_no_cancel_link() -> None:
    user = order_user()
    order = factory_online_order(
        user=user,
        status=Order.Status.PAYMENT_FAILED,
        online_amount=Decimal("230.00"),
        amount_due_on_delivery=Decimal("0.00"),
    )
    client = _client(user)

    html = client.get(reverse("order_detail", args=[order.checkout_group])).content.decode()

    assert "data-payment-failed" in html
    assert "Try again" in html
    assert "data-cancel-link" not in html


def test_a_paid_order_is_not_offered_payment_again() -> None:
    user = order_user()
    order = factory_online_order(
        user=user,
        status=Order.Status.CONFIRMED,
        online_amount=Decimal("230.00"),
        amount_due_on_delivery=Decimal("0.00"),
    )
    CapturedPaymentFactory(order=order)
    client = _client(user)

    html = client.get(reverse("order_detail", args=[order.checkout_group])).content.decode()

    assert "data-pay-now" not in html


def test_the_summary_uses_pay_online_before_capture() -> None:
    _user, order, _row = online_order()
    client = _client(order.user)

    html = client.get(reverse("order_detail", args=[order.checkout_group])).content.decode()
    label = re.search(r"<dt[^>]*>([^<]*Pay online[^<]*)</dt>", html)

    assert label is not None
    assert Payment.objects.filter(order=order).count() == 0
