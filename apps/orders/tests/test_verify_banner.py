"""Tests for the verification banner on the order pages (D71)."""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.orders.tests.checkout_helpers import clear_settings, reset_settings
from apps.orders.tests.factories import OrderFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before every test."""
    reset_settings()
    yield
    clear_settings()


def _detail_path(order) -> str:
    return reverse("order_detail", args=[order.checkout_group])


def _confirmation_path(order) -> str:
    return reverse("order_confirmation", args=[order.checkout_group])


def _has_element(html: str, marker: str) -> bool:
    """Whether any opening tag carries this data marker."""
    return re.search(rf"<[a-z0-9]+\b[^>]*\b{re.escape(marker)}\b", html) is not None


def _unverified_customer():
    return UserFactory(phone_verified_at=None, email_verified_at=None)


def test_detail_shows_the_banner_for_an_unverified_customer() -> None:
    user = _unverified_customer()
    order = OrderFactory(user=user)
    client = Client()
    client.force_login(user)

    html = client.get(_detail_path(order)).content.decode()

    assert _has_element(html, "data-verify-banner")


def test_detail_banner_links_to_the_phone_and_email_pages() -> None:
    user = _unverified_customer()
    order = OrderFactory(user=user)
    client = Client()
    client.force_login(user)

    html = client.get(_detail_path(order)).content.decode()

    assert f'href="{reverse("account_verify_phone")}"' in html
    assert f'href="{reverse("account_verify_email")}"' in html
    assert 'data-verify-kind="phone"' in html
    assert 'data-verify-kind="email"' in html


def test_detail_hides_the_banner_for_a_ready_customer() -> None:
    user = UserFactory()
    order = OrderFactory(user=user)
    client = Client()
    client.force_login(user)

    html = client.get(_detail_path(order)).content.decode()

    assert not _has_element(html, "data-verify-banner")


def test_confirmation_shows_the_banner_for_an_unverified_customer() -> None:
    user = _unverified_customer()
    order = OrderFactory(user=user)
    client = Client()
    client.force_login(user)

    html = client.get(_confirmation_path(order)).content.decode()

    assert _has_element(html, "data-verify-banner")


def test_confirmation_hides_the_banner_for_a_ready_customer() -> None:
    user = UserFactory()
    order = OrderFactory(user=user)
    client = Client()
    client.force_login(user)

    html = client.get(_confirmation_path(order)).content.decode()

    assert not _has_element(html, "data-verify-banner")


def test_banner_lists_only_the_missing_item() -> None:
    # The phone is verified, so only the email may be listed.
    user = UserFactory(phone_verified_at=timezone.now(), email_verified_at=None)
    order = OrderFactory(user=user)
    client = Client()
    client.force_login(user)

    html = client.get(_detail_path(order)).content.decode()

    assert _has_element(html, "data-verify-banner")
    assert 'data-verify-kind="email"' in html
    assert 'data-verify-kind="phone"' not in html
