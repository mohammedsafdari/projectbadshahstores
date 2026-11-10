"""Tests for restoring a failed online order's lines into the cart (D77)."""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest
from django.db import connection

from apps.accounts.models import User
from apps.cart.models import Cart, CartItem
from apps.cart.rules import MAX_PACKETS_PER_LINE
from apps.cart.services import add_item, restore_cart_from_order
from apps.cart.stores import DbCartStore
from apps.catalog.models import ProductVariant
from apps.inventory.services import adjust_stock
from apps.payments.tests.helpers import clear_settings, reset_settings, variant
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before every test."""
    reset_settings()
    yield
    clear_settings()


def test_an_empty_cart_gets_the_order_lines_back() -> None:
    user = UserFactory()
    row = variant()

    restored = restore_cart_from_order(user, [(row, Decimal("2"))])

    line = CartItem.objects.get(cart__user=user)
    assert restored == [row.pk]
    assert line.variant_id == row.pk
    assert line.quantity == Decimal("2.000")


def test_an_existing_line_is_left_alone() -> None:
    user = UserFactory()
    row = variant()
    add_item(DbCartStore(user), row, Decimal("1"))

    restored = restore_cart_from_order(user, [(row, Decimal("2"))])

    assert restored == []
    assert CartItem.objects.get(cart__user=user).quantity == Decimal("1.000")


def test_the_restored_quantity_is_clamped_to_available_stock() -> None:
    user = UserFactory()
    row = variant(stock="1.000")

    restore_cart_from_order(user, [(row, Decimal("2"))])

    assert CartItem.objects.get(cart__user=user).quantity == Decimal("1.000")


def test_the_restored_quantity_is_clamped_to_the_line_cap() -> None:
    user = UserFactory()
    row = variant(stock="200.000")

    restore_cart_from_order(user, [(row, Decimal("150"))])

    assert CartItem.objects.get(cart__user=user).quantity == Decimal(MAX_PACKETS_PER_LINE)


def test_a_line_that_is_no_longer_purchasable_is_skipped() -> None:
    user = UserFactory()
    row = variant()
    row.product.is_active = False
    row.product.save(update_fields=["is_active", "updated_at"])

    restored = restore_cart_from_order(user, [(row, Decimal("2"))])

    assert restored == []
    assert CartItem.objects.filter(cart__user=user).count() == 0


def test_a_line_with_no_stock_left_is_skipped() -> None:
    user = UserFactory()
    row = variant(stock="10.000")
    adjust_stock(variant=row, quantity_change=Decimal("-10.000"), reason="Test")

    restored = restore_cart_from_order(user, [(row, Decimal("2"))])

    assert restored == []
    assert CartItem.objects.filter(cart__user=user).count() == 0


def test_no_items_writes_nothing() -> None:
    user = UserFactory()

    assert restore_cart_from_order(user, []) == []
    assert CartItem.objects.filter(cart__user=user).count() == 0


def test_a_zero_quantity_is_ignored() -> None:
    user = UserFactory()
    row = variant()

    assert restore_cart_from_order(user, [(row, Decimal("0"))]) == []


def test_a_new_line_moves_the_cart_timestamp_and_leaves_the_old_line_alone() -> None:
    user = UserFactory()
    kept = variant()
    added = variant()
    add_item(DbCartStore(user), kept, Decimal("1"))
    cart = CartItem.objects.get(cart__user=user).cart
    kept_line = CartItem.objects.get(cart=cart)
    cart_before = cart.updated_at
    kept_before = kept_line.updated_at

    restored = restore_cart_from_order(
        user,
        [(kept, Decimal("2")), (added, Decimal("1"))],
    )

    cart.refresh_from_db()
    kept_line.refresh_from_db()
    added_line = CartItem.objects.get(cart=cart, variant=added)
    assert restored == [added.pk]
    assert cart.updated_at > cart_before
    assert kept_line.updated_at == kept_before
    assert kept_line.quantity == Decimal("1.000")
    assert added_line.quantity == Decimal("1.000")


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_restores_leave_one_line() -> None:
    user = UserFactory()
    row = variant()
    Cart.objects.get_or_create(user=user)
    user_pk = user.pk
    row_pk = row.pk
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []
    restored: list[list[int]] = []

    def restore(quantity: str) -> None:
        try:
            barrier.wait(timeout=10)
            thread_user = User.objects.get(pk=user_pk)
            thread_row = ProductVariant.objects.get(pk=row_pk)
            restored.append(restore_cart_from_order(thread_user, [(thread_row, Decimal(quantity))]))
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=restore, args=(quantity,)) for quantity in ("1", "2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    # Without the cart-row lock both threads would create a line and the
    # unique (cart, variant) constraint would refuse the second one.
    assert CartItem.objects.filter(cart__user=user).count() == 1
    assert CartItem.objects.get(cart__user=user).quantity in (
        Decimal("1.000"),
        Decimal("2.000"),
    )
    # One thread adds the line; the other finds it and reports nothing added.
    assert sorted(restored) == [[], [row_pk]]
