"""Tests for the order confirmation page (D66)."""

from __future__ import annotations

import re
from datetime import timedelta
from uuid import uuid4

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.orders.models import Order
from apps.orders.presentation import cod_on_delivery_sentence, substitution_label
from apps.orders.tests.factories import OrderFactory, OrderItemFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


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


def _confirmation_path(order: Order) -> str:
    return reverse("order_confirmation", args=[order.checkout_group])


def test_confirmation_redirects_a_guest_to_login() -> None:
    response = Client().get(reverse("order_confirmation", args=[uuid4()]))

    assert response.status_code == 302
    assert response.url.startswith(f"{reverse('account_login')}?next=")
    assert "next=/checkout/done/" in response.url


def test_confirmation_shows_the_order_number_and_items() -> None:
    user = UserFactory()
    order = OrderFactory(user=user)
    item = OrderItemFactory(order=order)
    client = Client()
    client.force_login(user)

    html = client.get(_confirmation_path(order)).content.decode()

    assert _element_text(html, "data-order-number") == order.order_number
    assert _element_text(html, "data-line-name") == item.product_name
    assert _element_text(html, "data-line-quantity") == (
        f"{item.quantity_ordered:g} x {item.pack_label}"
    )
    assert _element_text(html, "data-line-total") == f"₹{item.line_total:.2f}"


def test_confirmation_shows_totals_and_the_cod_amount() -> None:
    user = UserFactory()
    order = OrderFactory(user=user)
    OrderItemFactory(order=order)
    client = Client()
    client.force_login(user)

    html = client.get(_confirmation_path(order)).content.decode()

    assert _element_text(html, "data-item-total") == f"₹{order.item_total:.2f}"
    assert _element_text(html, "data-delivery-fee") == f"₹{order.delivery_fee:.2f}"
    assert _element_text(html, "data-grand-total") == f"₹{order.grand_total:.2f}"
    assert _element_text(html, "data-amount-due") == cod_on_delivery_sentence(
        order.amount_due_on_delivery
    )


def test_confirmation_shows_the_frozen_address() -> None:
    user = UserFactory()
    order = OrderFactory(user=user)
    OrderItemFactory(order=order)
    client = Client()
    client.force_login(user)

    html = client.get(_confirmation_path(order)).content.decode()

    address_text = _element_text(html, "data-ship-address")
    assert order.ship_name in address_text
    assert order.ship_phone in address_text
    assert order.ship_line1 in address_text
    assert order.ship_city in address_text
    assert order.ship_state in address_text
    assert order.ship_pincode in address_text


def test_confirmation_says_delivery_today() -> None:
    user = UserFactory()
    order = OrderFactory(user=user, promised_delivery_date=timezone.localdate())
    OrderItemFactory(order=order)
    client = Client()
    client.force_login(user)

    html = client.get(_confirmation_path(order)).content.decode()

    assert _element_text(html, "data-delivery-day") == "Today"


def test_confirmation_says_delivery_tomorrow() -> None:
    user = UserFactory()
    tomorrow = timezone.localdate() + timedelta(days=1)
    order = OrderFactory(user=user, promised_delivery_date=tomorrow)
    OrderItemFactory(order=order)
    client = Client()
    client.force_login(user)

    html = client.get(_confirmation_path(order)).content.decode()

    assert _element_text(html, "data-delivery-day") == "Tomorrow"


def test_confirmation_shows_the_substitution_choice_and_note() -> None:
    user = UserFactory()
    order = OrderFactory(
        user=user,
        substitution_preference=Order.SubstitutionPreference.REFUND,
        customer_note="Ring the bell twice.",
    )
    OrderItemFactory(order=order)
    client = Client()
    client.force_login(user)

    html = client.get(_confirmation_path(order)).content.decode()

    assert _element_text(html, "data-substitution") == substitution_label(
        Order.SubstitutionPreference.REFUND
    )
    assert _element_text(html, "data-note") == "Ring the bell twice."


def test_confirmation_of_another_users_order_is_404() -> None:
    order = OrderFactory()
    client = Client()
    client.force_login(UserFactory())

    response = client.get(_confirmation_path(order))

    assert response.status_code == 404


def test_confirmation_of_an_unknown_group_is_404() -> None:
    client = Client()
    client.force_login(UserFactory())

    response = client.get(reverse("order_confirmation", args=[uuid4()]))

    assert response.status_code == 404


def test_confirmation_is_never_cached() -> None:
    user = UserFactory()
    order = OrderFactory(user=user)
    client = Client()
    client.force_login(user)

    response = client.get(_confirmation_path(order))

    assert "no-store" in response["Cache-Control"]
