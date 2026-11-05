"""Tests for the lazy header count (D61)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext

from apps.cart.context_processors import cart_line_count
from apps.cart.stores import GUEST_CART_SESSION_KEY
from apps.cart.tests.factories import CartFactory, CartItemFactory
from apps.catalog.tests.factories import ProductVariantFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _request(user=None):
    request = RequestFactory().get("/")
    request.user = user if user is not None else AnonymousUser()
    request.session = SessionStore()
    return request


def _count(request) -> int:
    context = cart_line_count(request)
    return context["cart_line_count"]()


def test_guest_with_no_cart_has_count_zero() -> None:
    assert _count(_request()) == 0


def test_guest_count_is_the_number_of_lines() -> None:
    first = ProductVariantFactory()
    second = ProductVariantFactory()
    request = _request()
    request.session[GUEST_CART_SESSION_KEY] = {
        str(first.pk): "1.000",
        str(second.pk): "2.000",
    }

    assert _count(request) == 2


def test_customer_count_is_the_number_of_lines() -> None:
    user = UserFactory()
    cart = CartFactory(user=user)
    for _ in range(3):
        CartItemFactory(cart=cart, variant=ProductVariantFactory(), quantity=Decimal("1.000"))

    assert _count(_request(user)) == 3


def test_count_is_lines_not_quantity() -> None:
    user = UserFactory()
    cart = CartFactory(user=user)
    CartItemFactory(cart=cart, variant=ProductVariantFactory(), quantity=Decimal("5.000"))

    assert _count(_request(user)) == 1


def test_guest_count_uses_no_query() -> None:
    variant = ProductVariantFactory()
    request = _request()
    request.session[GUEST_CART_SESSION_KEY] = {str(variant.pk): "1.000"}

    with CaptureQueriesContext(connection) as ctx:
        assert _count(request) == 1

    assert len(ctx.captured_queries) == 0


def test_count_is_not_computed_when_the_template_does_not_print_it() -> None:
    user = UserFactory()
    cart = CartFactory(user=user)
    CartItemFactory(cart=cart, variant=ProductVariantFactory(), quantity=Decimal("1.000"))
    request = _request(user)

    # The processor only builds the callable; the query must wait until a
    # template actually asks for the number.
    with CaptureQueriesContext(connection) as ctx:
        context = cart_line_count(request)

    assert callable(context["cart_line_count"])
    assert len(ctx.captured_queries) == 0
