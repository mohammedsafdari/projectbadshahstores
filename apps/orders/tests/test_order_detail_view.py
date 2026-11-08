"""Tests for the customer order detail page (Slice 38d, D70)."""

from __future__ import annotations

import re
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.orders.models import Order
from apps.orders.presentation import (
    CANCEL_ORDER_LINK,
    VIEW_THIS_ORDER_LINK,
    cod_on_delivery_sentence,
    credit_confirmation_sentence,
    credit_returned_sentence,
    delivery_day_text,
    order_date_text,
    payment_method_label,
    status_sentence,
    substitution_label,
    timeline_time_text,
)
from apps.orders.services import CUSTOMER_CANCELLABLE, transition_order
from apps.orders.state import OUT_FOR_DELIVERY, PACKING
from apps.orders.tests.checkout_helpers import (
    NOW,
    address,
    clear_settings,
    fill_cart,
    place_cod,
    reset_settings,
    serve,
    set_settings,
    variant,
)
from apps.orders.tests.factories import OrderFactory, OrderItemFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before every test."""
    reset_settings()
    yield
    clear_settings()


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


def _element_texts(html: str, marker: str) -> list[str]:
    """The text of every element with this marker, in page order."""
    pattern = (
        rf"<(?P<tag>[a-z0-9]+)\b[^>]*\b{re.escape(marker)}\b[^>]*>"
        rf"(?P<body>.*?)</(?P=tag)>"
    )
    # A comprehension builds the plain-text list in one line.
    matches = re.finditer(pattern, html, re.DOTALL)
    return [_strip_tags(match.group("body")).strip() for match in matches]


def _element_tag(html: str, marker: str) -> str:
    """The opening tag of the first element that carries this data marker."""
    pattern = rf"<[a-z0-9]+\b[^>]*\b{re.escape(marker)}\b[^>]*>"
    match = re.search(pattern, html)
    assert match is not None, f"No element with {marker}."
    return match.group(0)


def _has_element(html: str, marker: str) -> bool:
    """Whether any element carries this data marker."""
    return re.search(rf"\b{re.escape(marker)}\b", html) is not None


def _detail_path(order: Order) -> str:
    return reverse("order_detail", args=[order.checkout_group])


def _status_kwargs(status: str) -> dict[str, object]:
    """The timestamps the check constraints require for these statuses."""
    if status == Order.Status.CANCELLED:
        return {"cancelled_at": timezone.now()}
    if status == Order.Status.DELIVERED:
        return {"delivered_at": timezone.now()}
    return {}


def _cod_order():
    """A real COD order for a served customer with stock in the cart."""
    user = UserFactory()
    serve()
    address_row = address(user)
    fill_cart(user, variant())
    return user, place_cod(user, address_row).order


def test_detail_requires_login() -> None:
    order = OrderFactory()

    response = Client().get(_detail_path(order))

    assert response.status_code == 302
    assert response.url.startswith(f"{reverse('account_login')}?next=")
    assert "next=/orders/" in response.url


def test_detail_of_another_customers_order_is_404() -> None:
    order = OrderFactory()
    client = Client()
    client.force_login(UserFactory())

    assert client.get(_detail_path(order)).status_code == 404


def test_detail_of_an_unknown_group_is_404() -> None:
    client = Client()
    client.force_login(UserFactory())

    assert client.get(reverse("order_detail", args=[uuid4()])).status_code == 404


def test_detail_shows_number_status_items_totals_address_and_payment() -> None:
    user = UserFactory()
    order = OrderFactory(user=user)
    item = OrderItemFactory(order=order)
    client = Client()
    client.force_login(user)

    html = client.get(_detail_path(order)).content.decode()

    assert _element_text(html, "data-order-number") == order.order_number
    assert _element_text(html, "data-order-status") == status_sentence(order.status)
    assert _element_text(html, "data-delivery-day") == delivery_day_text(
        order.promised_delivery_date
    )
    assert _element_text(html, "data-placed-on") == order_date_text(order.created_at)
    assert _element_text(html, "data-line-name") == item.product_name
    assert _element_text(html, "data-item-total") == f"₹{order.item_total:.2f}"
    assert _element_text(html, "data-grand-total") == f"₹{order.grand_total:.2f}"
    assert _element_text(html, "data-amount-due") == cod_on_delivery_sentence(
        order.amount_due_on_delivery
    )
    assert _element_text(html, "data-substitution") == substitution_label(
        order.substitution_preference
    )
    address_text = _element_text(html, "data-ship-address")
    assert order.ship_name in address_text
    assert order.ship_phone in address_text
    assert order.ship_line1 in address_text
    assert order.ship_city in address_text
    assert order.ship_state in address_text
    assert order.ship_pincode in address_text


def test_detail_of_a_credit_order_shows_the_khata_sentence() -> None:
    user = UserFactory()
    order = OrderFactory(user=user, payment_method=Order.PaymentMethod.CREDIT)
    OrderItemFactory(order=order)
    client = Client()
    client.force_login(user)

    html = client.get(_detail_path(order)).content.decode()

    assert _element_text(html, "data-payment-method") == payment_method_label(
        Order.PaymentMethod.CREDIT
    )
    assert _element_text(html, "data-amount-due") == credit_confirmation_sentence(order.grand_total)


def test_detail_timeline_lists_every_status_change_in_order() -> None:
    user, order = _cod_order()
    staff = UserFactory()
    transition_order(order.pk, PACKING, actor=staff, now=NOW)
    transition_order(order.pk, OUT_FOR_DELIVERY, actor=staff, now=NOW)
    client = Client()
    client.force_login(user)

    html = client.get(_detail_path(order)).content.decode()

    assert _element_texts(html, "data-timeline-label") == [
        "Order placed",
        "Order confirmed",
        "Packing your order",
        "Out for delivery",
    ]
    assert _element_texts(html, "data-timeline-time") == [
        timeline_time_text(log.created_at) for log in order.status_logs.all()
    ]


def test_detail_timeline_hides_notes_actors_and_cancel_reasons() -> None:
    user, order = _cod_order()
    staff = UserFactory()
    transition_order(
        order.pk,
        Order.Status.CANCELLED,
        actor=staff,
        note="INTERNAL-NOTE-MARKER",
        reason="INTERNAL-REASON-MARKER",
        now=NOW,
    )
    client = Client()
    client.force_login(user)

    html = client.get(_detail_path(order)).content.decode()

    assert "INTERNAL-NOTE-MARKER" not in html
    assert "INTERNAL-REASON-MARKER" not in html


def test_detail_timeline_uses_customer_wording_not_status_codes() -> None:
    user, order = _cod_order()
    client = Client()
    client.force_login(user)

    html = client.get(_detail_path(order)).content.decode()

    assert _element_texts(html, "data-timeline-label") == ["Order placed", "Order confirmed"]
    assert "PLACED" not in html
    assert "CONFIRMED" not in html


def test_detail_shows_the_cancel_link_while_placed_or_confirmed() -> None:
    user = UserFactory()
    client = Client()
    client.force_login(user)

    for status in CUSTOMER_CANCELLABLE:
        order = OrderFactory(user=user, status=status)
        html = client.get(_detail_path(order)).content.decode()

        tag = _element_tag(html, "data-cancel-link")
        assert f'href="{reverse("order_cancel", args=[order.checkout_group])}"' in tag
        assert _element_text(html, "data-cancel-link") == str(CANCEL_ORDER_LINK)


def test_detail_hides_the_cancel_link_for_every_other_status() -> None:
    user = UserFactory()
    client = Client()
    client.force_login(user)
    # A comprehension keeps every status the customer may not cancel.
    others = [status for status in Order.Status.values if status not in CUSTOMER_CANCELLABLE]

    for status in others:
        order = OrderFactory(user=user, status=status, **_status_kwargs(status))
        html = client.get(_detail_path(order)).content.decode()

        assert not _has_element(html, "data-cancel-link")


def test_detail_of_a_cancelled_cod_order_says_cancelled_and_shows_no_credit_sentence() -> None:
    user = UserFactory()
    order = OrderFactory(
        user=user,
        status=Order.Status.CANCELLED,
        cancelled_at=timezone.now(),
        payment_method=Order.PaymentMethod.COD,
    )
    client = Client()
    client.force_login(user)

    html = client.get(_detail_path(order)).content.decode()

    assert _element_text(html, "data-order-status") == status_sentence(Order.Status.CANCELLED)
    assert not _has_element(html, "data-credit-returned")


def test_detail_of_a_cancelled_credit_order_says_the_credit_was_returned() -> None:
    user = UserFactory()
    order = OrderFactory(
        user=user,
        status=Order.Status.CANCELLED,
        cancelled_at=timezone.now(),
        payment_method=Order.PaymentMethod.CREDIT,
        grand_total=Decimal("230.00"),
    )
    client = Client()
    client.force_login(user)

    html = client.get(_detail_path(order)).content.decode()

    assert _element_text(html, "data-credit-returned") == credit_returned_sentence(
        Decimal("230.00")
    )


def test_detail_whatsapp_link_has_digits_only_number_and_order_number() -> None:
    set_settings(whatsapp_number="+91 98765 43210")
    user = UserFactory()
    order = OrderFactory(user=user)
    client = Client()
    client.force_login(user)

    html = client.get(_detail_path(order)).content.decode()

    tag = _element_tag(html, "data-whatsapp-link")
    assert 'href="https://wa.me/919876543210?text=' in tag
    assert "%2F" in tag
    assert 'target="_blank"' in tag
    assert 'rel="noopener"' in tag


def test_detail_hides_the_whatsapp_link_when_the_number_is_blank() -> None:
    set_settings(whatsapp_number="")
    user = UserFactory()
    order = OrderFactory(user=user)
    client = Client()
    client.force_login(user)

    html = client.get(_detail_path(order)).content.decode()

    assert not _has_element(html, "data-whatsapp-link")


def test_detail_hides_the_whatsapp_link_when_the_number_is_invalid() -> None:
    set_settings(whatsapp_number="12345")
    user = UserFactory()
    order = OrderFactory(user=user)
    client = Client()
    client.force_login(user)

    html = client.get(_detail_path(order)).content.decode()

    assert not _has_element(html, "data-whatsapp-link")


def test_detail_query_count_does_not_grow_with_items_and_logs() -> None:
    serve()
    small_user = UserFactory()
    small_address = address(small_user)
    fill_cart(small_user, variant())
    small_order = place_cod(small_user, small_address).order

    big_user = UserFactory()
    big_address = address(big_user)
    for _ in range(3):
        fill_cart(big_user, variant())
    big_order = place_cod(big_user, big_address).order
    staff = UserFactory()
    transition_order(big_order.pk, PACKING, actor=staff, now=NOW)
    transition_order(big_order.pk, OUT_FOR_DELIVERY, actor=staff, now=NOW)

    small_client = Client()
    small_client.force_login(small_user)
    big_client = Client()
    big_client.force_login(big_user)
    # Warm both clients so the first request's one-off work is not counted.
    small_client.get(_detail_path(small_order))
    big_client.get(_detail_path(big_order))

    with CaptureQueriesContext(connection) as small_queries:
        small_client.get(_detail_path(small_order))
    with CaptureQueriesContext(connection) as big_queries:
        big_client.get(_detail_path(big_order))

    assert len(small_queries.captured_queries) == len(big_queries.captured_queries)


def test_detail_is_not_cached() -> None:
    user = UserFactory()
    order = OrderFactory(user=user)
    client = Client()
    client.force_login(user)

    response = client.get(_detail_path(order))

    assert "no-store" in response["Cache-Control"]


def test_detail_rejects_post() -> None:
    user = UserFactory()
    order = OrderFactory(user=user)
    client = Client()
    client.force_login(user)

    assert client.post(_detail_path(order)).status_code == 405


def test_confirmation_page_links_to_the_order_detail() -> None:
    user = UserFactory()
    order = OrderFactory(user=user)
    client = Client()
    client.force_login(user)

    html = client.get(reverse("order_confirmation", args=[order.checkout_group])).content.decode()

    tag = _element_tag(html, "data-view-order-link")
    assert f'href="{_detail_path(order)}"' in tag
    assert _element_text(html, "data-view-order-link") == str(VIEW_THIS_ORDER_LINK)
