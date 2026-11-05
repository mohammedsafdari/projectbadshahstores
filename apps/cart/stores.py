"""Where a cart lives: the database for a customer, the session for a guest.

Both stores expose the same two methods, so the cart services never care which
one they were given. The guest cart holds only ``{variant_id: "quantity"}``:
JSON-safe strings, no Decimal objects, no prices and no names. Names and prices
are read live from the catalogue on every page.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.base import SessionBase
from django.db import transaction

from apps.accounts.models import User
from apps.cart.models import Cart, CartItem
from apps.catalog.models import ProductVariant

# The one session key the guest cart lives under (D60).
GUEST_CART_SESSION_KEY = "guest_cart"

QUANTITY_PLACES = Decimal("0.001")


@dataclass(frozen=True)
class StoredLine:
    """One line as stored: the variant object and its quantity."""

    variant: ProductVariant
    quantity: Decimal


def _parse_quantity(value: object) -> Decimal | None:
    """Turn one session value into a quantity, or None when it is corrupt.

    The session is client-side data that the shop cannot trust, so a bad entry
    is dropped instead of raising on a page view. Only strings are accepted,
    because that is what the store writes and what JSON keeps exactly.
    """
    if not isinstance(value, str):
        return None
    try:
        quantity = Decimal(value)
    except InvalidOperation:
        return None
    if not quantity.is_finite() or quantity <= 0:
        return None
    try:
        quantised = quantity.quantize(QUANTITY_PLACES)
    except InvalidOperation:
        return None
    if quantity != quantised:
        return None
    return quantised


def read_guest_items(session: SessionBase) -> dict[int, Decimal]:
    """Read the guest cart, dropping corrupt entries and keeping good ones.

    Insertion order is kept, so the cart shows lines in the order they were
    added. A session key that is not a dict, a key that is not a positive
    integer, or a quantity that is not a positive finite Decimal with at most
    three decimals is ignored.
    """
    raw = session.get(GUEST_CART_SESSION_KEY)
    if not isinstance(raw, dict):
        return {}

    items: dict[int, Decimal] = {}
    for key, value in raw.items():
        if isinstance(key, bool):
            continue
        try:
            variant_id = int(key)
        except (TypeError, ValueError):
            continue
        if variant_id <= 0:
            continue
        quantity = _parse_quantity(value)
        if quantity is None:
            continue
        items[variant_id] = quantity
    return items


def _write_guest_items(session: SessionBase, items: dict[int, Decimal]) -> None:
    """Replace the whole session value and mark the session as changed.

    Django compares the session at the start and end of a request. It does not
    notice a change made *inside* a nested dict (``session[key][id] = ...``),
    so this assigns a new dict and sets ``modified`` explicitly. That is the
    idiom the cart store relies on.
    """
    session[GUEST_CART_SESSION_KEY] = {
        str(variant_id): str(quantity) for variant_id, quantity in items.items()
    }
    session.modified = True


def clear_guest_cart(session: SessionBase) -> None:
    """Remove the guest cart from the session."""
    session.pop(GUEST_CART_SESSION_KEY, None)
    session.modified = True


class CartStore(ABC):
    """What the cart services need from a place that holds cart lines."""

    @abstractmethod
    def lines(self) -> list[StoredLine]:
        """Return the current lines, in the order they were added."""

    @abstractmethod
    def change(self, change: Callable[[dict[int, Decimal]], None]) -> None:
        """Apply ``change`` to a copy of the lines and save the result.

        ``change`` receives an ordered ``{variant_id: quantity}`` dict and may
        set or delete entries. If it raises, nothing is saved. For a saved cart
        the whole read-change-write runs under one row lock.
        """


class DbCartStore(CartStore):
    """The saved cart of a logged-in customer."""

    def __init__(self, user: User) -> None:
        self.user = user

    def lines(self) -> list[StoredLine]:
        return [
            StoredLine(variant=item.variant, quantity=item.quantity)
            for item in CartItem.objects.filter(cart__user=self.user)
            # The cart page reads the product, its brand and its photos. One
            # join plus one prefetch keeps the query count flat per line.
            .select_related("variant__product__brand")
            .prefetch_related("variant__product__images")
            .order_by("created_at", "pk")
        ]

    def change(self, change: Callable[[dict[int, Decimal]], None]) -> None:
        with transaction.atomic():
            # The cart is created on the first change, then locked. Locking a
            # row that does not exist yet would lock nothing, which is why the
            # create comes first and the lock is a second read.
            cart, _created = Cart.objects.get_or_create(user=self.user)
            cart = Cart.objects.select_for_update().get(pk=cart.pk)

            existing = {
                item.variant_id: item for item in cart.items.all().order_by("created_at", "pk")
            }
            items = {variant_id: item.quantity for variant_id, item in existing.items()}
            before = dict(items)

            change(items)

            # A refused change raises above this line, so an exception leaves
            # the stored rows exactly as they were.
            if items == before:
                return

            for variant_id, quantity in items.items():
                item = existing.get(variant_id)
                if item is None:
                    CartItem.objects.create(
                        cart=cart,
                        variant_id=variant_id,
                        quantity=quantity,
                    )
                elif item.quantity != quantity:
                    item.quantity = quantity
                    # auto_now only writes updated_at when the field is in
                    # update_fields, so it must be listed.
                    item.save(update_fields=["quantity", "updated_at"])

            for variant_id, item in existing.items():
                if variant_id not in items:
                    item.delete()

            cart.save(update_fields=["updated_at"])


class SessionCartStore(CartStore):
    """The guest cart in ``request.session``."""

    def __init__(self, session: SessionBase) -> None:
        self.session = session

    def lines(self) -> list[StoredLine]:
        items = read_guest_items(self.session)
        if not items:
            return []
        # One query loads every variant, then the session order is restored.
        by_id = {
            variant.pk: variant
            for variant in ProductVariant.objects.filter(pk__in=items)
            # Same joins as the saved cart so the page costs the same for a
            # guest and a customer.
            .select_related("product__brand")
            .prefetch_related("product__images")
        }
        return [
            StoredLine(variant=by_id[variant_id], quantity=quantity)
            for variant_id, quantity in items.items()
            if variant_id in by_id
        ]

    def change(self, change: Callable[[dict[int, Decimal]], None]) -> None:
        items = read_guest_items(self.session)
        change(items)
        _write_guest_items(self.session, items)


def get_store(user: User | AnonymousUser, session: SessionBase) -> CartStore:
    """Return the database store for a customer, the session store otherwise."""
    if isinstance(user, User) and user.is_authenticated:
        return DbCartStore(user)
    return SessionCartStore(session)
