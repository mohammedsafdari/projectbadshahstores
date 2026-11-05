"""Tests for merging the guest cart on login (D60)."""

from __future__ import annotations

import threading
import time
from decimal import Decimal

import pytest
from django.db import connection, transaction

from apps.accounts.models import User
from apps.cart.models import Cart, CartItem
from apps.cart.services import merge_guest_cart
from apps.cart.tests.factories import CartFactory, CartItemFactory
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.tests.factories import InventoryMovementFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _packet(stock: str = "10.000"):
    variant = ProductVariantFactory(is_active=True)
    # A zero movement is not allowed by the ledger, so leave the variant empty.
    if Decimal(stock):
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
    # A zero movement is not allowed by the ledger, so leave the variant empty.
    if Decimal(stock):
        InventoryMovementFactory(variant=variant, quantity_change=Decimal(stock))
    return variant


def test_guest_lines_are_added_to_an_empty_saved_cart() -> None:
    user = UserFactory()
    variant = _packet("10.000")

    result = merge_guest_cart(user, {variant.pk: Decimal("2.000")})

    item = CartItem.objects.get(cart__user=user)
    assert item.variant_id == variant.pk
    assert item.quantity == Decimal("2.000")
    assert result.merged == [variant.pk]


def test_same_variant_quantities_are_added() -> None:
    user = UserFactory()
    variant = _packet("10.000")
    cart = CartFactory(user=user)
    CartItemFactory(cart=cart, variant=variant, quantity=Decimal("2.000"))

    result = merge_guest_cart(user, {variant.pk: Decimal("3.000")})

    assert CartItem.objects.get(cart=cart).quantity == Decimal("5.000")
    assert result.merged == [variant.pk]


def test_the_sum_is_capped_at_the_available_amount() -> None:
    user = UserFactory()
    variant = _packet("5.000")
    cart = CartFactory(user=user)
    CartItemFactory(cart=cart, variant=variant, quantity=Decimal("2.000"))

    result = merge_guest_cart(user, {variant.pk: Decimal("10.000")})

    assert CartItem.objects.get(cart=cart).quantity == Decimal("5.000")
    assert result.adjusted == [variant.pk]
    assert result.merged == []


def test_the_sum_is_capped_at_the_line_limit() -> None:
    user = UserFactory()
    variant = _loose("100.000")

    result = merge_guest_cart(user, {variant.pk: Decimal("60.000")})

    assert CartItem.objects.get(cart__user=user).quantity == Decimal("50.000")
    assert result.adjusted == [variant.pk]


def test_a_capped_loose_quantity_is_rounded_down_to_the_step() -> None:
    user = UserFactory()
    variant = _loose("1.250", step="0.100")
    cart = CartFactory(user=user)
    CartItemFactory(cart=cart, variant=variant, quantity=Decimal("0.500"))

    result = merge_guest_cart(user, {variant.pk: Decimal("1.000")})

    # 0.5 + 1.0 capped at 1.25, then rounded down to a whole 0.1 step.
    assert CartItem.objects.get(cart=cart).quantity == Decimal("1.200")
    assert result.adjusted == [variant.pk]


def test_a_line_with_nothing_available_is_dropped_and_reported() -> None:
    user = UserFactory()
    variant = _packet("0.000")

    result = merge_guest_cart(user, {variant.pk: Decimal("1.000")})

    assert result.dropped == [variant.pk]
    assert CartItem.objects.filter(cart__user=user).count() == 0


def test_an_archived_variant_is_dropped_and_reported() -> None:
    user = UserFactory()
    variant = ProductVariantFactory(is_active=False)

    result = merge_guest_cart(user, {variant.pk: Decimal("1.000")})

    assert result.dropped == [variant.pk]
    assert CartItem.objects.filter(cart__user=user).count() == 0


def test_a_guest_entry_with_an_invalid_quantity_is_dropped() -> None:
    user = UserFactory()
    variant = _packet("10.000")

    result = merge_guest_cart(user, {variant.pk: Decimal("1.500")})

    assert result.dropped == [variant.pk]
    assert CartItem.objects.filter(cart__user=user).count() == 0


def test_the_result_lists_merged_adjusted_and_dropped_variants() -> None:
    user = UserFactory()
    normal = _packet("10.000")
    capped = _packet("2.000")
    archived = ProductVariantFactory(is_active=False)

    result = merge_guest_cart(
        user,
        {
            normal.pk: Decimal("1.000"),
            capped.pk: Decimal("5.000"),
            archived.pk: Decimal("1.000"),
        },
    )

    assert result.merged == [normal.pk]
    assert result.adjusted == [capped.pk]
    assert result.dropped == [archived.pk]
    assert result.needs_notice is True


def test_an_empty_guest_cart_creates_no_cart_row() -> None:
    user = UserFactory()

    result = merge_guest_cart(user, {})

    assert result.merged == []
    assert result.adjusted == []
    assert result.dropped == []
    assert result.needs_notice is False
    assert Cart.objects.filter(user=user).count() == 0


def test_saved_lines_not_in_the_guest_cart_are_untouched() -> None:
    user = UserFactory()
    saved_variant = _packet("10.000")
    guest_variant = _packet("10.000")
    cart = CartFactory(user=user)
    saved_item = CartItemFactory(cart=cart, variant=saved_variant, quantity=Decimal("2.000"))

    merge_guest_cart(user, {guest_variant.pk: Decimal("1.000")})

    saved_item.refresh_from_db()
    assert saved_item.quantity == Decimal("2.000")
    assert CartItem.objects.get(cart=cart, variant=guest_variant).quantity == Decimal("1.000")


def test_a_second_merge_with_nothing_changes_nothing() -> None:
    user = UserFactory()
    variant = _packet("10.000")
    merge_guest_cart(user, {variant.pk: Decimal("2.000")})
    item = CartItem.objects.get(cart__user=user)
    before = item.updated_at

    result = merge_guest_cart(user, {})

    item.refresh_from_db()
    assert item.quantity == Decimal("2.000")
    assert item.updated_at == before
    assert result.merged == []


@pytest.mark.django_db(transaction=True)
def test_merge_waits_for_a_lock_held_on_the_cart() -> None:
    user = UserFactory()
    variant = _packet("100.000")
    cart = CartFactory(user=user)
    item = CartItemFactory(cart=cart, variant=variant, quantity=Decimal("2.000"))
    user_pk = user.pk
    variant_pk = variant.pk
    cart_pk = cart.pk
    item_pk = item.pk

    locked = threading.Event()
    errors: list[BaseException] = []

    def hold_the_lock() -> None:
        try:
            with transaction.atomic():
                User.objects.select_for_update().get(pk=user_pk)
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

    def merge_while_locked() -> None:
        try:
            assert locked.wait(timeout=10)
            merger = User.objects.get(pk=user_pk)
            locked_variant = ProductVariant.objects.get(pk=variant_pk)
            merge_guest_cart(merger, {locked_variant.pk: Decimal("1.000")})
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    holder = threading.Thread(target=hold_the_lock)
    merger = threading.Thread(target=merge_while_locked)
    holder.start()
    merger.start()
    holder.join(timeout=30)
    merger.join(timeout=30)

    assert not errors, errors
    # Without the cart lock the merge reads 2 and writes 3, overwriting the 5.
    assert CartItem.objects.get(pk=item_pk).quantity == Decimal("6.000")


def test_merge_does_not_change_the_guest_items_it_was_given() -> None:
    user = UserFactory()
    variant = _packet("10.000")
    guest_items = {variant.pk: Decimal("2.000")}
    before = dict(guest_items)

    merge_guest_cart(user, guest_items)

    assert guest_items == before
    assert guest_items[variant.pk] == Decimal("2.000")
