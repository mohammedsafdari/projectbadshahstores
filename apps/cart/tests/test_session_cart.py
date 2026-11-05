"""Tests for the guest cart in the session (D60)."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.cart.rules import (
    InsufficientAvailabilityError,
    InvalidCartQuantityError,
    NotPurchasableError,
)
from apps.cart.services import add_item, clear_cart, remove_item, set_item_quantity
from apps.cart.stores import (
    GUEST_CART_SESSION_KEY,
    SessionCartStore,
    clear_guest_cart,
    read_guest_items,
)
from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.tests.factories import InventoryMovementFactory

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


def _session_store() -> tuple[SessionStore, SessionCartStore]:
    session = SessionStore()
    return session, SessionCartStore(session)


def test_add_stores_the_quantity_as_a_string_under_the_variant_id() -> None:
    variant = _packet()
    session, store = _session_store()

    add_item(store, variant, 2)

    assert session[GUEST_CART_SESSION_KEY] == {str(variant.pk): "2.000"}


def test_add_adds_to_an_existing_entry() -> None:
    variant = _packet()
    session, store = _session_store()
    add_item(store, variant, 1)

    add_item(store, variant, 2)

    assert session[GUEST_CART_SESSION_KEY] == {str(variant.pk): "3.000"}


def test_set_replaces_the_quantity() -> None:
    variant = _packet()
    session, store = _session_store()
    add_item(store, variant, 1)

    set_item_quantity(store, variant, 4)

    assert session[GUEST_CART_SESSION_KEY] == {str(variant.pk): "4.000"}


def test_set_to_zero_removes_the_entry() -> None:
    variant = _packet()
    session, store = _session_store()
    add_item(store, variant, 1)

    set_item_quantity(store, variant, 0)

    assert session[GUEST_CART_SESSION_KEY] == {}


def test_remove_and_clear() -> None:
    first = _packet()
    second = _packet()
    session, store = _session_store()
    add_item(store, first, 1)
    add_item(store, second, 2)

    remove_item(store, first)

    assert session[GUEST_CART_SESSION_KEY] == {str(second.pk): "2.000"}

    clear_cart(store)

    assert session[GUEST_CART_SESSION_KEY] == {}


def test_the_cart_survives_a_session_save_and_reload() -> None:
    variant = _packet()
    session, store = _session_store()
    add_item(store, variant, 2)
    session.save()

    reloaded = SessionStore(session_key=session.session_key)

    lines = SessionCartStore(reloaded).lines()
    assert len(lines) == 1
    assert lines[0].variant.pk == variant.pk
    assert lines[0].quantity == Decimal("2.000")


def test_add_refuses_more_than_available() -> None:
    variant = _packet("2.000")
    session, store = _session_store()

    with pytest.raises(InsufficientAvailabilityError):
        add_item(store, variant, 3)

    assert session.get(GUEST_CART_SESSION_KEY) is None


def test_add_refuses_an_archived_variant() -> None:
    variant = ProductVariantFactory(is_active=False)
    session, store = _session_store()

    with pytest.raises(NotPurchasableError):
        add_item(store, variant, 1)

    assert session.get(GUEST_CART_SESSION_KEY) is None


def test_add_refuses_a_quantity_the_step_does_not_allow() -> None:
    variant = _packet()

    with pytest.raises(InvalidCartQuantityError):
        add_item(_session_store()[1], variant, Decimal("1.500"))


def test_the_session_holds_no_prices_or_names() -> None:
    variant = _packet()
    session, store = _session_store()

    add_item(store, variant, 1)

    payload = session[GUEST_CART_SESSION_KEY]
    # JSON can store this value exactly: string keys and string quantities.
    encoded = json.dumps(payload)
    assert list(payload) == [str(variant.pk)]
    assert list(payload.values()) == ["1.000"]
    assert variant.product.name_en not in encoded
    assert "price" not in encoded.lower()


def test_corrupt_session_data_that_is_not_a_dict_is_ignored() -> None:
    session, store = _session_store()
    session[GUEST_CART_SESSION_KEY] = ["1", "2"]

    assert read_guest_items(session) == {}
    assert store.lines() == []


def test_corrupt_entries_are_dropped_and_good_ones_kept() -> None:
    variant = _packet()
    session, _store = _session_store()
    session[GUEST_CART_SESSION_KEY] = {
        str(variant.pk): "2.000",
        "not-an-id": "1.000",
        "1": "not-a-number",
        "2": "0",
        "3": "-1",
        "4": "NaN",
        "5": "1.2345",
    }

    items = read_guest_items(session)

    assert items == {variant.pk: Decimal("2.000")}


def test_a_refused_add_leaves_the_session_unchanged() -> None:
    variant = _packet("5.000")
    session, store = _session_store()
    add_item(store, variant, 1)

    with pytest.raises(InsufficientAvailabilityError):
        add_item(store, variant, 10)

    assert session[GUEST_CART_SESSION_KEY] == {str(variant.pk): "1.000"}


def test_clear_guest_cart_removes_the_session_key() -> None:
    variant = _packet()
    session, store = _session_store()
    add_item(store, variant, 1)

    clear_guest_cart(session)

    assert GUEST_CART_SESSION_KEY not in session


def test_read_guest_items_returns_variant_ids_and_decimals_in_order() -> None:
    first = ProductVariantFactory()
    second = ProductVariantFactory()
    session, _store = _session_store()
    session[GUEST_CART_SESSION_KEY] = {
        str(second.pk): "2.000",
        str(first.pk): "0.500",
    }

    items = read_guest_items(session)

    assert list(items) == [second.pk, first.pk]
    assert items[second.pk] == Decimal("2.000")
    assert items[first.pk] == Decimal("0.500")


def test_session_lines_load_product_and_brand_without_extra_queries() -> None:
    session, store = _session_store()
    first = _packet()
    session[GUEST_CART_SESSION_KEY] = {str(first.pk): "1.000"}

    with CaptureQueriesContext(connection) as one:
        one_lines = store.lines()
        for line in one_lines:
            assert line.variant.product.brand.name
            list(line.variant.product.images.all())

    extras = [_packet() for _ in range(9)]
    session[GUEST_CART_SESSION_KEY] = {str(variant.pk): "1.000" for variant in [first, *extras]}

    with CaptureQueriesContext(connection) as ten:
        ten_lines = store.lines()
        for line in ten_lines:
            assert line.variant.product.brand.name
            list(line.variant.product.images.all())

    assert len(one_lines) == 1
    assert len(ten_lines) == 10
    assert len(one.captured_queries) == len(ten.captured_queries)
