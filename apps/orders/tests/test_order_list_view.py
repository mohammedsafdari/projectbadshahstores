"""Tests for the "My orders" list page and its header link (D70)."""

from __future__ import annotations

import re

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.orders.models import Order
from apps.orders.presentation import (
    NO_ORDERS_MESSAGE,
    START_SHOPPING_LINK,
    VIEW_ORDER_LINK,
    item_count_text,
    money,
    order_date_text,
    page_label,
    payment_method_label,
    status_sentence,
)
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


def _element_texts(html: str, marker: str) -> list[str]:
    """The text of every element with this marker, in page order."""
    pattern = (
        rf"<(?P<tag>[a-z0-9]+)\b[^>]*\b{re.escape(marker)}\b[^>]*>"
        rf"(?P<body>.*?)</(?P=tag)>"
    )
    # finditer walks the matches one by one; the comprehension turns each
    # match's body into plain text.
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


def _order_list_html(client: Client, params: dict[str, str] | None = None) -> str:
    return client.get(reverse("order_list"), params or {}).content.decode()


def test_order_list_requires_login() -> None:
    response = Client().get(reverse("order_list"))

    assert response.status_code == 302
    assert response.url.startswith(f"{reverse('account_login')}?next=")
    assert "next=/orders/" in response.url


def test_order_list_shows_only_the_customers_own_orders() -> None:
    user = UserFactory()
    own = OrderFactory(user=user)
    OrderFactory()

    client = Client()
    client.force_login(user)

    assert _element_texts(_order_list_html(client), "data-order-number") == [own.order_number]


def test_order_list_is_newest_first() -> None:
    user = UserFactory()
    first = OrderFactory(user=user)
    second = OrderFactory(user=user)

    client = Client()
    client.force_login(user)

    assert _element_texts(_order_list_html(client), "data-order-number") == [
        second.order_number,
        first.order_number,
    ]


def test_order_list_row_shows_number_status_total_payment_and_item_count() -> None:
    user = UserFactory()
    order = OrderFactory(user=user)
    OrderItemFactory(order=order)
    OrderItemFactory(order=order)

    client = Client()
    client.force_login(user)
    html = _order_list_html(client)

    assert _element_text(html, "data-order-number") == order.order_number
    assert _element_text(html, "data-order-date") == order_date_text(order.created_at)
    assert _element_text(html, "data-order-status") == status_sentence(order.status)
    assert _element_text(html, "data-order-total") == money(order.grand_total)
    assert _element_text(html, "data-order-payment") == payment_method_label(order.payment_method)
    assert _element_text(html, "data-order-items") == item_count_text(2)


def test_order_list_row_links_to_the_order_detail_page() -> None:
    user = UserFactory()
    order = OrderFactory(user=user)

    client = Client()
    client.force_login(user)
    html = _order_list_html(client)

    expected = reverse("order_detail", args=[order.checkout_group])
    assert f'href="{expected}"' in _element_tag(html, "data-order-link")
    assert _element_text(html, "data-order-link") == str(VIEW_ORDER_LINK)


def test_order_list_row_status_follows_the_order_status() -> None:
    user = UserFactory()
    cancelled = OrderFactory(
        user=user,
        status=Order.Status.CANCELLED,
        cancelled_at=timezone.now(),
    )
    delivered = OrderFactory(
        user=user,
        status=Order.Status.DELIVERED,
        delivered_at=timezone.now(),
    )

    client = Client()
    client.force_login(user)
    html = _order_list_html(client)

    status_texts = _element_texts(html, "data-order-status")
    assert status_texts == [
        status_sentence(delivered.status),
        status_sentence(cancelled.status),
    ]
    assert status_sentence(Order.Status.PLACED) not in status_texts


def test_order_list_paginates_ten_per_page() -> None:
    user = UserFactory()
    # 25 orders: page 1 holds the 10 newest, page 3 holds the 5 oldest.
    created = [OrderFactory(user=user) for _ in range(25)]
    newest_first = list(reversed(created))

    client = Client()
    client.force_login(user)
    first_page = _order_list_html(client)
    third_page = _order_list_html(client, {"page": "3"})

    assert _element_texts(first_page, "data-order-number") == [
        order.order_number for order in newest_first[:10]
    ]
    assert _element_texts(third_page, "data-order-number") == [
        order.order_number for order in newest_first[20:]
    ]
    assert _element_text(first_page, "data-page-label") == page_label(1, 3)
    assert _element_text(third_page, "data-page-label") == page_label(3, 3)
    assert _has_element(first_page, "data-page-next")
    assert not _has_element(first_page, "data-page-previous")
    assert _has_element(third_page, "data-page-previous")
    assert not _has_element(third_page, "data-page-next")
    assert 'href="?page=2"' in _element_tag(first_page, "data-page-next")
    assert 'href="?page=2"' in _element_tag(third_page, "data-page-previous")


def test_order_list_bad_page_number_shows_a_valid_page() -> None:
    user = UserFactory()
    for _ in range(25):
        OrderFactory(user=user)

    client = Client()
    client.force_login(user)
    bad_page = _order_list_html(client, {"page": "abc"})
    too_large_page = _order_list_html(client, {"page": "999"})

    assert _element_text(bad_page, "data-page-label") == page_label(1, 3)
    assert len(_element_texts(bad_page, "data-order-number")) == 10
    assert _element_text(too_large_page, "data-page-label") == page_label(3, 3)
    assert len(_element_texts(too_large_page, "data-order-number")) == 5


def test_order_list_empty_state_has_a_shop_link() -> None:
    client = Client()
    client.force_login(UserFactory())

    html = _order_list_html(client)

    assert not _has_element(html, "data-order-row")
    assert _element_text(html, "data-empty-orders").startswith(str(NO_ORDERS_MESSAGE))
    assert _element_text(html, "data-start-shopping") == str(START_SHOPPING_LINK)
    assert f'href="{reverse("product_list")}"' in _element_tag(html, "data-start-shopping")


def test_order_list_query_count_does_not_grow_with_orders() -> None:
    small_user = UserFactory()
    OrderFactory(user=small_user)
    big_user = UserFactory()
    for _ in range(10):
        OrderFactory(user=big_user)

    small_client = Client()
    small_client.force_login(small_user)
    big_client = Client()
    big_client.force_login(big_user)
    # Warm both clients so the first request's one-off work is not counted.
    small_client.get(reverse("order_list"))
    big_client.get(reverse("order_list"))

    with CaptureQueriesContext(connection) as small_queries:
        small_client.get(reverse("order_list"))
    with CaptureQueriesContext(connection) as big_queries:
        big_client.get(reverse("order_list"))

    assert len(small_queries.captured_queries) == len(big_queries.captured_queries)


def test_order_list_is_not_cached() -> None:
    client = Client()
    client.force_login(UserFactory())

    response = client.get(reverse("order_list"))

    assert "no-store" in response["Cache-Control"]


def test_order_list_rejects_post() -> None:
    client = Client()
    client.force_login(UserFactory())

    assert client.post(reverse("order_list")).status_code == 405


def test_order_list_labels_credit_orders() -> None:
    user = UserFactory()
    OrderFactory(user=user, payment_method=Order.PaymentMethod.CREDIT)

    client = Client()
    client.force_login(user)

    assert _element_text(_order_list_html(client), "data-order-payment") == payment_method_label(
        Order.PaymentMethod.CREDIT
    )


def test_header_shows_my_orders_link_for_a_logged_in_customer() -> None:
    client = Client()
    client.force_login(UserFactory())

    html = client.get(reverse("home")).content.decode()

    assert f'href="{reverse("order_list")}"' in _element_tag(html, "data-my-orders-link")


def test_header_hides_my_orders_link_for_a_guest() -> None:
    html = Client().get(reverse("home")).content.decode()

    assert not _has_element(html, "data-my-orders-link")
