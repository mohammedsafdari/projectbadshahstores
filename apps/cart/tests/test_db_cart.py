"""Tests for the database cart store and its services (D60)."""

from __future__ import annotations

import threading
import time
from decimal import Decimal

import pytest
from django.db import connection, transaction

from apps.accounts.models import User
from apps.cart.models import Cart, CartItem
from apps.cart.rules import (
    InsufficientAvailabilityError,
    InvalidCartQuantityError,
    NotPurchasableError,
    QuantityTooLargeError,
)
from apps.cart.services import add_item, clear_cart, remove_item, set_item_quantity
from apps.cart.stores import DbCartStore
from apps.cart.tests.factories import CartFactory, CartItemFactory
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.tests.factories import InventoryMovementFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _packet(stock: str = "10.000"):
    variant = ProductVariantFactory(is_active=True)
    InventoryMovementFactory(variant=variant, quantity_change=Decimal(stock))
    return variant


def _loose(stock: str = "100.000", step: str = "0.001"):
    variant = ProductVariantFactory(
        is_active=True,
        is_sold_loose=True,
        unit="KG",
        pack_size=Decimal("1.000"),
        quantity_step=Decimal(step),
    )
    InventoryMovementFactory(variant=variant, quantity_change=Decimal(stock))
    return variant


def test_add_creates_the_cart_and_the_line() -> None:
    user = UserFactory()
    variant = _packet()

    add_item(DbCartStore(user), variant, 2)

    cart = Cart.objects.get(user=user)
    item = cart.items.get()
    assert item.variant_id == variant.pk
    assert item.quantity == Decimal("2.000")


def test_add_to_an_existing_line_adds_the_quantities() -> None:
    user = UserFactory()
    variant = _packet()
    store = DbCartStore(user)
    add_item(store, variant, 1)

    add_item(store, variant, 2)

    assert CartItem.objects.get(cart__user=user).quantity == Decimal("3.000")


def test_set_replaces_the_quantity() -> None:
    user = UserFactory()
    variant = _packet()
    store = DbCartStore(user)
    add_item(store, variant, 1)

    set_item_quantity(store, variant, 4)

    assert CartItem.objects.get(cart__user=user).quantity == Decimal("4.000")


def test_set_to_zero_removes_the_line() -> None:
    user = UserFactory()
    variant = _packet()
    store = DbCartStore(user)
    add_item(store, variant, 1)

    set_item_quantity(store, variant, 0)

    assert CartItem.objects.filter(cart__user=user).count() == 0
    assert Cart.objects.filter(user=user).exists()


def test_remove_deletes_the_line() -> None:
    user = UserFactory()
    variant = _packet()
    store = DbCartStore(user)
    add_item(store, variant, 2)

    remove_item(store, variant)

    assert CartItem.objects.filter(cart__user=user).count() == 0


def test_remove_of_a_missing_line_does_nothing() -> None:
    user = UserFactory()
    cart = CartFactory(user=user)
    variant = _packet()

    remove_item(DbCartStore(user), variant)

    assert CartItem.objects.filter(cart=cart).count() == 0


def test_clear_removes_every_line_but_keeps_the_cart() -> None:
    user = UserFactory()
    store = DbCartStore(user)
    add_item(store, _packet(), 1)
    add_item(store, _packet(), 2)

    clear_cart(store)

    assert CartItem.objects.filter(cart__user=user).count() == 0
    assert Cart.objects.filter(user=user).exists()


def test_add_refuses_more_than_is_available_and_reports_the_available_amount() -> None:
    user = UserFactory()
    variant = _packet("5.000")

    with pytest.raises(InsufficientAvailabilityError) as caught:
        add_item(DbCartStore(user), variant, 6)

    assert caught.value.available == Decimal("5.000")
    assert CartItem.objects.filter(cart__user=user).count() == 0


def test_add_counts_the_quantity_already_in_the_cart_against_the_limit() -> None:
    user = UserFactory()
    variant = _packet("5.000")
    store = DbCartStore(user)
    add_item(store, variant, 3)

    with pytest.raises(InsufficientAvailabilityError):
        add_item(store, variant, 3)

    assert CartItem.objects.get(cart__user=user).quantity == Decimal("3.000")


def test_set_refuses_more_than_is_available() -> None:
    user = UserFactory()
    variant = _packet("5.000")
    store = DbCartStore(user)
    add_item(store, variant, 1)

    with pytest.raises(InsufficientAvailabilityError):
        set_item_quantity(store, variant, 6)

    assert CartItem.objects.get(cart__user=user).quantity == Decimal("1.000")


def test_set_may_lower_a_line_that_is_above_the_available_amount() -> None:
    user = UserFactory()
    variant = _packet("5.000")
    cart = CartFactory(user=user)
    CartItemFactory(cart=cart, variant=variant, quantity=Decimal("10.000"))

    set_item_quantity(DbCartStore(user), variant, 3)

    assert CartItem.objects.get(cart=cart).quantity == Decimal("3.000")


def test_add_refuses_an_archived_variant() -> None:
    user = UserFactory()
    variant = ProductVariantFactory(is_active=False)

    with pytest.raises(NotPurchasableError):
        add_item(DbCartStore(user), variant, 1)

    assert CartItem.objects.filter(cart__user=user).count() == 0


def test_add_refuses_a_quantity_the_step_does_not_allow() -> None:
    user = UserFactory()
    variant = _packet()

    with pytest.raises(InvalidCartQuantityError):
        add_item(DbCartStore(user), variant, Decimal("1.500"))


def test_add_refuses_more_than_the_line_limit() -> None:
    user = UserFactory()
    variant = _loose("100.000")

    with pytest.raises(QuantityTooLargeError):
        add_item(DbCartStore(user), variant, Decimal("51"))


def test_a_refused_add_leaves_the_cart_unchanged() -> None:
    user = UserFactory()
    variant = _packet("5.000")
    store = DbCartStore(user)
    add_item(store, variant, 2)

    with pytest.raises(InsufficientAvailabilityError):
        add_item(store, variant, 10)

    item = CartItem.objects.get(cart__user=user)
    assert item.quantity == Decimal("2.000")
    assert CartItem.objects.filter(cart__user=user).count() == 1


def test_two_users_have_separate_carts() -> None:
    first_user = UserFactory()
    second_user = UserFactory()
    first_variant = _packet()
    second_variant = _packet()

    add_item(DbCartStore(first_user), first_variant, 1)
    add_item(DbCartStore(second_user), second_variant, 2)

    assert CartItem.objects.get(cart__user=first_user).variant_id == first_variant.pk
    assert CartItem.objects.get(cart__user=second_user).variant_id == second_variant.pk


def test_changing_a_line_moves_updated_at_on_the_line() -> None:
    user = UserFactory()
    variant = _packet()
    store = DbCartStore(user)
    add_item(store, variant, 1)
    item = CartItem.objects.get(cart__user=user)
    before = item.updated_at

    set_item_quantity(store, variant, 3)

    item.refresh_from_db()
    assert item.quantity == Decimal("3.000")
    assert item.updated_at > before


def test_changing_a_line_moves_updated_at_on_the_cart() -> None:
    user = UserFactory()
    store = DbCartStore(user)
    add_item(store, _packet(), 1)
    cart = Cart.objects.get(user=user)
    before = cart.updated_at

    add_item(store, _packet(), 1)

    cart.refresh_from_db()
    assert cart.updated_at > before


# ---------------------------------------------------------------------------
# Locks: the cart row serialises read-modify-write
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_add_waits_for_a_lock_held_on_the_cart() -> None:
    user = UserFactory()
    variant = _packet()
    cart = CartFactory(user=user)
    item = CartItemFactory(cart=cart, variant=variant, quantity=Decimal("2.000"))
    cart_pk = cart.pk
    item_pk = item.pk
    variant_pk = variant.pk
    user_pk = user.pk

    locked = threading.Event()
    errors: list[BaseException] = []

    def hold_the_lock() -> None:
        try:
            with transaction.atomic():
                Cart.objects.select_for_update().get(pk=cart_pk)
                locked_item = CartItem.objects.get(pk=item_pk)
                locked_item.quantity = Decimal("5.000")
                locked_item.save(update_fields=["quantity", "updated_at"])
                locked.set()
                time.sleep(0.5)
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    def add_while_locked() -> None:
        try:
            assert locked.wait(timeout=10)
            adder = User.objects.get(pk=user_pk)
            locked_variant = ProductVariant.objects.get(pk=variant_pk)
            add_item(DbCartStore(adder), locked_variant, 1)
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    holder = threading.Thread(target=hold_the_lock)
    adder = threading.Thread(target=add_while_locked)
    holder.start()
    adder.start()
    holder.join(timeout=30)
    adder.join(timeout=30)

    assert not errors, errors
    # Without the cart lock the adder reads 2 and writes 3, overwriting the 5.
    assert CartItem.objects.get(pk=item_pk).quantity == Decimal("6.000")


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_adds_are_both_counted() -> None:
    user = UserFactory()
    variant = _packet()
    cart = CartFactory(user=user)
    item = CartItemFactory(cart=cart, variant=variant, quantity=Decimal("1.000"))
    user_pk = user.pk
    variant_pk = variant.pk

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def add_one() -> None:
        try:
            barrier.wait(timeout=10)
            added = User.objects.get(pk=user_pk)
            locked_variant = ProductVariant.objects.get(pk=variant_pk)
            add_item(DbCartStore(added), locked_variant, 1)
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=add_one) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    # Without the lock both reads see 1 and the second write wins: 2, not 3.
    assert CartItem.objects.get(pk=item.pk).quantity == Decimal("3.000")
