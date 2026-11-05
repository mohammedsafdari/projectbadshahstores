"""Tests for the cart URL names (D61)."""

from __future__ import annotations

from django.urls import reverse


def test_cart_detail_url() -> None:
    assert reverse("cart_detail") == "/cart/"


def test_cart_add_url() -> None:
    assert reverse("cart_add") == "/cart/add/"


def test_cart_update_url() -> None:
    assert reverse("cart_update") == "/cart/update/"


def test_cart_remove_url() -> None:
    assert reverse("cart_remove") == "/cart/remove/"
