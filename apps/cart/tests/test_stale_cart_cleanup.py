"""The stale saved-cart clean-up job and its beat entry (D94)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.cart.models import Cart, CartItem
from apps.cart.tasks import delete_stale_saved_carts
from apps.cart.tests.factories import CartFactory, CartItemFactory
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory

pytestmark = pytest.mark.django_db


def _age_cart(cart: Cart, days: int) -> None:
    """Move one cart's updated_at back in time.

    A queryset update() runs no save(), which is why the auto_now field keeps
    the value written here instead of being refreshed on the way to the
    database.
    """
    Cart.objects.filter(pk=cart.pk).update(updated_at=timezone.now() - timedelta(days=days))


def _age_cart_and_lines(cart: Cart, days: int) -> None:
    """Move a cart and every line in it back in time, with no save() either."""
    moment = timezone.now() - timedelta(days=days)
    Cart.objects.filter(pk=cart.pk).update(updated_at=moment)
    CartItem.objects.filter(cart_id=cart.pk).update(updated_at=moment)


def test_cart_untouched_for_31_days_is_deleted() -> None:
    # The numbers are written out, not read from the module: a test that used
    # the constant would follow any change to it and catch nothing.
    cart = CartFactory()
    CartItemFactory(cart=cart, quantity=Decimal("1.000"))
    _age_cart_and_lines(cart, 31)
    cart_pk = cart.pk

    deleted = delete_stale_saved_carts()

    assert deleted == 1
    assert not Cart.objects.filter(pk=cart_pk).exists()


def test_empty_cart_untouched_for_31_days_is_deleted() -> None:
    # A customer who removed every line still keeps an empty cart row, so the
    # job must delete it without needing a line to prove the cart is old.
    cart = CartFactory()
    _age_cart(cart, 31)
    cart_pk = cart.pk

    deleted = delete_stale_saved_carts()

    assert deleted == 1
    assert not Cart.objects.filter(pk=cart_pk).exists()


def test_cart_exactly_30_days_old_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    frozen = timezone.now()
    cart = CartFactory()
    item = CartItemFactory(cart=cart, quantity=Decimal("1.000"))
    # The cart is exactly on the cutoff and its line is older still, so only
    # the "strictly older" rule can keep it. A fixed clock makes "exactly"
    # exact, instead of racing the real clock by a few microseconds.
    Cart.objects.filter(pk=cart.pk).update(updated_at=frozen - timedelta(days=30))
    CartItem.objects.filter(pk=item.pk).update(updated_at=frozen - timedelta(days=31))
    monkeypatch.setattr("apps.cart.tasks.timezone", SimpleNamespace(now=lambda: frozen))
    cart_pk = cart.pk

    deleted = delete_stale_saved_carts()

    assert deleted == 0
    assert Cart.objects.filter(pk=cart_pk).exists()


def test_recent_cart_is_kept() -> None:
    cart = CartFactory()
    CartItemFactory(cart=cart, quantity=Decimal("1.000"))
    cart_pk = cart.pk

    deleted = delete_stale_saved_carts()

    assert deleted == 0
    assert Cart.objects.filter(pk=cart_pk).exists()


def test_old_cart_with_a_recent_item_is_kept() -> None:
    cart = CartFactory()
    item = CartItemFactory(cart=cart, quantity=Decimal("1.000"))
    # Only the cart row is old: adding or changing a line does not always touch
    # it, so the line's own timestamp is what keeps the cart.
    _age_cart(cart, 31)
    cart_pk = cart.pk

    deleted = delete_stale_saved_carts()

    assert deleted == 0
    assert Cart.objects.filter(pk=cart_pk).exists()
    assert CartItem.objects.filter(pk=item.pk).exists()


def test_deleting_a_cart_removes_its_items_and_keeps_the_user_and_the_variants() -> None:
    cart = CartFactory()
    user_pk = cart.user_id
    variant = ProductVariantFactory()
    CartItemFactory(cart=cart, variant=variant, quantity=Decimal("1.000"))
    CartItemFactory(cart=cart, quantity=Decimal("2.000"))
    _age_cart_and_lines(cart, 31)
    cart_pk = cart.pk
    variant_pk = variant.pk

    delete_stale_saved_carts()

    assert not Cart.objects.filter(pk=cart_pk).exists()
    assert not CartItem.objects.filter(cart_id=cart_pk).exists()
    assert User.objects.filter(pk=user_pk).exists()
    assert ProductVariant.objects.filter(pk=variant_pk).exists()


def test_task_returns_the_number_of_carts_deleted() -> None:
    first = CartFactory()
    CartItemFactory(cart=first, quantity=Decimal("1.000"))
    CartItemFactory(cart=first, variant=ProductVariantFactory(), quantity=Decimal("2.000"))
    second = CartFactory()
    CartItemFactory(cart=second, quantity=Decimal("3.000"))
    _age_cart_and_lines(first, 31)
    _age_cart_and_lines(second, 31)

    deleted = delete_stale_saved_carts()

    # Two carts go and three lines with them: the answer is the cart count, not
    # the number of rows the delete touched.
    assert deleted == 2


def test_task_on_an_empty_database_returns_zero() -> None:
    assert delete_stale_saved_carts() == 0


def test_beat_entry_runs_at_0345(settings) -> None:
    entry = settings.CELERY_BEAT_SCHEDULE["delete-stale-saved-carts"]

    assert entry["task"] == "apps.cart.tasks.delete_stale_saved_carts"
    assert entry["schedule"].hour == {3}
    assert entry["schedule"].minute == {45}
