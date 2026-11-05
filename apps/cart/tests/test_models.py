"""Tests for the cart models and their constraints (D60)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.cart.models import Cart, CartItem
from apps.cart.tests.factories import CartFactory, CartItemFactory
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_a_user_has_at_most_one_cart() -> None:
    user = UserFactory()
    CartFactory(user=user)

    # The inner atomic() keeps the test usable after the database error.
    with pytest.raises(IntegrityError), transaction.atomic():
        CartFactory(user=user)

    assert Cart.objects.filter(user=user).count() == 1


def test_cart_item_quantity_must_be_positive() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        CartItemFactory(quantity=Decimal("0.000"))


def test_a_variant_appears_once_per_cart() -> None:
    cart = CartFactory()
    variant = ProductVariantFactory()
    CartItemFactory(cart=cart, variant=variant, quantity=Decimal("1.000"))

    with pytest.raises(IntegrityError), transaction.atomic():
        CartItemFactory(cart=cart, variant=variant, quantity=Decimal("2.000"))


def test_the_same_variant_can_be_in_two_carts() -> None:
    variant = ProductVariantFactory()
    first = CartItemFactory(variant=variant)
    second = CartItemFactory(variant=variant)

    assert first.cart_id != second.cart_id
    assert CartItem.objects.filter(variant=variant).count() == 2


def test_quantity_keeps_three_decimals() -> None:
    item = CartItemFactory(quantity=Decimal("1.5"))

    item.refresh_from_db()
    assert item.quantity == Decimal("1.500")
    assert item.quantity.as_tuple().exponent == -3


def test_deleting_a_variant_with_cart_lines_is_refused() -> None:
    variant = ProductVariantFactory()
    variant_pk = variant.pk
    CartItemFactory(variant=variant)

    with pytest.raises(ProtectedError):
        variant.delete()

    assert ProductVariant.objects.filter(pk=variant_pk).exists()


def test_deleting_the_user_deletes_the_cart_and_its_lines() -> None:
    item = CartItemFactory()
    user = item.cart.user
    user_pk = user.pk

    user.delete()

    assert Cart.objects.filter(user_id=user_pk).count() == 0
    assert CartItem.objects.filter(pk=item.pk).count() == 0


def test_constraints_have_their_documented_names() -> None:
    names = {constraint.name for constraint in CartItem._meta.constraints}

    assert names == {"cart_item_quantity_gt_0", "cart_item_unique_variant_per_cart"}


def test_cart_and_line_timestamps_are_set() -> None:
    item = CartItemFactory()
    cart = item.cart

    assert cart.created_at is not None
    assert cart.updated_at is not None
    assert item.created_at is not None
    assert item.updated_at is not None
