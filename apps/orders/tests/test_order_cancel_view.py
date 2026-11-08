"""Tests for the customer cancel page (Slice 38d, D70)."""

from __future__ import annotations

import re
from decimal import Decimal
from unittest.mock import call, patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.credit.models import CreditAccount, CreditEntry
from apps.notifications.messages import EVENT_CANCELLED
from apps.orders.models import Order, OrderStatusLog
from apps.orders.presentation import (
    ALREADY_CANCELLED_MESSAGE,
    CANCEL_CONFIRM_BUTTON,
    CANCEL_HELP,
    CANCEL_NOT_POSSIBLE_MESSAGE,
    CANCEL_REASON_TOO_LONG,
    KEEP_ORDER_LINK,
    ORDER_CANCELLED_MESSAGE,
    cancel_question,
)
from apps.orders.services import cancel_order_by_customer, transition_order
from apps.orders.state import PACKING
from apps.orders.tests.checkout_helpers import (
    NOW,
    address,
    clear_settings,
    credit_user,
    fill_cart,
    place_cod,
    place_credit,
    reset_settings,
    serve,
    variant,
)
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


def _element_tag(html: str, marker: str) -> str:
    """The opening tag of the first element that carries this data marker."""
    pattern = rf"<[a-z0-9]+\b[^>]*\b{re.escape(marker)}\b[^>]*>"
    match = re.search(pattern, html)
    assert match is not None, f"No element with {marker}."
    return match.group(0)


def _form_with_action(html: str, action: str) -> str:
    """Return the first complete <form> whose action is ``action``."""
    pattern = rf'<form[^>]*action="{re.escape(action)}"[^>]*>.*?</form>'
    match = re.search(pattern, html, re.DOTALL)
    assert match is not None, f"No form with action {action}."
    return match.group(0)


def _cancel_path(order: Order) -> str:
    return reverse("order_cancel", args=[order.checkout_group])


def _detail_path(order: Order) -> str:
    return reverse("order_detail", args=[order.checkout_group])


def _cod_order():
    """A real COD order for a served customer with stock in the cart."""
    user = UserFactory()
    serve()
    address_row = address(user)
    fill_cart(user, variant())
    return user, place_cod(user, address_row).order


def _credit_order():
    """A real credit order for a served customer with stock in the cart."""
    user = credit_user()
    serve()
    address_row = address(user)
    fill_cart(user, variant())
    return user, place_credit(user, address_row).order


def test_cancel_page_requires_login() -> None:
    _user, order = _cod_order()

    get = Client().get(_cancel_path(order))
    post = Client().post(_cancel_path(order), {"reason": ""})

    assert get.status_code == 302
    assert get.url.startswith(f"{reverse('account_login')}?next=")
    assert "next=/orders/" in get.url
    assert post.status_code == 302
    assert post.url.startswith(f"{reverse('account_login')}?next=")


def test_cancel_page_of_another_customers_order_is_404_and_changes_nothing() -> None:
    _user, order = _cod_order()
    client = Client()
    client.force_login(UserFactory())

    get = client.get(_cancel_path(order))
    post = client.post(_cancel_path(order), {"reason": "Changed my mind"})

    assert get.status_code == 404
    assert post.status_code == 404
    order.refresh_from_db()
    assert order.status == Order.Status.CONFIRMED


def test_cancel_get_shows_a_confirmation_form_with_csrf_token_and_post_method() -> None:
    user, order = _cod_order()
    client = Client()
    client.force_login(user)

    html = client.get(_cancel_path(order)).content.decode()

    form = _form_with_action(html, _cancel_path(order))
    assert 'method="post"' in form
    assert 'name="csrfmiddlewaretoken"' in form
    assert 'name="reason"' in form
    assert 'maxlength="200"' in form
    assert _element_text(html, "data-cancel-question") == cancel_question(order.order_number)
    assert str(CANCEL_HELP) in html
    assert _element_text(html, "data-cancel-confirm") == str(CANCEL_CONFIRM_BUTTON)
    tag = _element_tag(html, "data-keep-order")
    assert f'href="{_detail_path(order)}"' in tag
    assert _element_text(html, "data-keep-order") == str(KEEP_ORDER_LINK)


def test_cancel_get_for_a_packing_order_redirects_to_the_detail_with_a_message() -> None:
    user, order = _cod_order()
    transition_order(order.pk, PACKING, actor=UserFactory(), now=NOW)
    client = Client()
    client.force_login(user)

    response = client.get(_cancel_path(order), follow=True)

    assert response.redirect_chain[-1][0].endswith(_detail_path(order))
    assert str(CANCEL_NOT_POSSIBLE_MESSAGE) in response.content.decode()


def test_cancel_get_for_a_cancelled_order_redirects_with_the_already_cancelled_message() -> None:
    user, order = _cod_order()
    cancel_order_by_customer(user, order.pk, now=NOW)
    client = Client()
    client.force_login(user)

    response = client.get(_cancel_path(order), follow=True)

    assert response.redirect_chain[-1][0].endswith(_detail_path(order))
    assert str(ALREADY_CANCELLED_MESSAGE) in response.content.decode()


def test_cancel_get_never_cancels_the_order() -> None:
    user, order = _cod_order()
    client = Client()
    client.force_login(user)

    response = client.get(_cancel_path(order))

    assert response.status_code == 200
    order.refresh_from_db()
    assert order.status == Order.Status.CONFIRMED
    assert not OrderStatusLog.objects.filter(
        order=order,
        to_status=Order.Status.CANCELLED,
    ).exists()


def test_cancel_post_cancels_a_confirmed_cod_order_and_redirects_to_the_detail() -> None:
    user, order = _cod_order()
    client = Client()
    client.force_login(user)

    response = client.post(_cancel_path(order), {"reason": ""}, follow=True)

    assert response.redirect_chain[-1][0].endswith(_detail_path(order))
    assert str(ORDER_CANCELLED_MESSAGE) in response.content.decode()
    order.refresh_from_db()
    assert order.status == Order.Status.CANCELLED
    assert order.cancelled_at is not None


def test_cancel_post_stores_the_optional_reason() -> None:
    user, order = _cod_order()
    client = Client()
    client.force_login(user)

    client.post(_cancel_path(order), {"reason": "  Changed my mind  "})

    order.refresh_from_db()
    assert order.cancel_reason == "Changed my mind"


def test_cancel_post_without_a_reason_is_allowed() -> None:
    user, order = _cod_order()
    client = Client()
    client.force_login(user)

    client.post(_cancel_path(order), {"reason": ""})

    order.refresh_from_db()
    assert order.status == Order.Status.CANCELLED
    assert order.cancel_reason == ""


def test_cancel_post_with_a_reason_over_200_characters_re_renders_and_changes_nothing() -> None:
    user, order = _cod_order()
    client = Client()
    client.force_login(user)

    response = client.post(_cancel_path(order), {"reason": "x" * 201})

    assert response.status_code == 200
    html = response.content.decode()
    assert _element_text(html, "data-reason-error") == str(CANCEL_REASON_TOO_LONG)
    order.refresh_from_db()
    assert order.status == Order.Status.CONFIRMED


def test_cancel_post_for_a_credit_order_gives_the_credit_back() -> None:
    user, order = _credit_order()
    client = Client()
    client.force_login(user)

    client.post(_cancel_path(order), {"reason": ""})

    assert CreditAccount.objects.get(user=user).balance == Decimal("0.00")
    assert (
        CreditEntry.objects.filter(
            order=order,
            entry_type=CreditEntry.EntryType.CANCEL_REVERSAL,
        ).count()
        == 1
    )


def test_cancel_post_twice_cancels_once_and_reverses_the_credit_once() -> None:
    user, order = _credit_order()
    client = Client()
    client.force_login(user)

    client.post(_cancel_path(order), {"reason": ""})
    second = client.post(_cancel_path(order), {"reason": ""}, follow=True)

    order.refresh_from_db()
    assert order.status == Order.Status.CANCELLED
    assert (
        CreditEntry.objects.filter(
            order=order,
            entry_type=CreditEntry.EntryType.CANCEL_REVERSAL,
        ).count()
        == 1
    )
    assert (
        OrderStatusLog.objects.filter(
            order=order,
            to_status=Order.Status.CANCELLED,
        ).count()
        == 1
    )
    assert str(ALREADY_CANCELLED_MESSAGE) in second.content.decode()


def test_cancel_post_after_packing_changes_nothing_and_shows_a_message() -> None:
    user, order = _cod_order()
    transition_order(order.pk, PACKING, actor=UserFactory(), now=NOW)
    client = Client()
    client.force_login(user)

    response = client.post(_cancel_path(order), {"reason": ""}, follow=True)

    order.refresh_from_db()
    assert order.status == PACKING
    assert str(CANCEL_NOT_POSSIBLE_MESSAGE) in response.content.decode()


def test_cancel_post_without_a_csrf_token_is_refused() -> None:
    user, order = _cod_order()
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)

    response = client.post(_cancel_path(order), {"reason": ""})

    assert response.status_code == 403
    order.refresh_from_db()
    assert order.status == Order.Status.CONFIRMED


def test_cancel_post_sends_the_cancelled_notice_after_commit(
    django_capture_on_commit_callbacks,
) -> None:
    user, order = _cod_order()
    client = Client()
    client.force_login(user)

    with (
        patch("apps.orders.services.notify_order_event") as notify,
        django_capture_on_commit_callbacks(execute=True) as callbacks,
    ):
        response = client.post(_cancel_path(order), {"reason": ""})

    assert response.status_code == 302
    assert len(callbacks) == 1
    assert notify.call_args_list == [call(order.pk, EVENT_CANCELLED)]


def test_cancel_get_for_a_staff_moved_order_after_page_opened_is_handled() -> None:
    user, order = _cod_order()
    client = Client()
    client.force_login(user)
    assert client.get(_cancel_path(order)).status_code == 200

    transition_order(order.pk, PACKING, actor=UserFactory(), now=NOW)
    response = client.post(_cancel_path(order), {"reason": ""}, follow=True)

    order.refresh_from_db()
    assert order.status == PACKING
    assert str(CANCEL_NOT_POSSIBLE_MESSAGE) in response.content.decode()
