"""Tests for live cart totals (D60).

The settings tests use values that differ from the defaults (fee 45, free
delivery at 800, minimum 150), so a hard-coded 30, 500 or 200 fails.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.cart.services import (
    STATUS_BAD_QUANTITY,
    STATUS_NOT_ENOUGH_STOCK,
    STATUS_OUT_OF_STOCK,
    STATUS_UNAVAILABLE,
    add_item,
    build_cart,
    set_item_quantity,
)
from apps.cart.stores import DbCartStore, SessionCartStore
from apps.cart.tests.factories import CartFactory, CartItemFactory
from apps.catalog.tests.factories import ProductVariantFactory
from apps.core.models import ShopSettings
from apps.core.services import get_shop_settings
from apps.inventory.tests.factories import InventoryMovementFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """The locmem cache outlives a test's database transaction, so empty it."""
    cache.clear()
    yield
    cache.clear()


def _variant(
    price: str = "100.00",
    mrp: str | None = None,
    stock: str = "100.000",
    loose: bool = False,
    step: str = "1",
):
    variant = ProductVariantFactory(
        is_active=True,
        selling_price=Decimal(price),
        mrp=Decimal(mrp if mrp is not None else price),
        is_sold_loose=loose,
        unit="KG" if loose else "PACKET",
        pack_size=Decimal("1.000"),
        quantity_step=Decimal(step),
    )
    # A zero movement is not allowed by the ledger, so leave the variant empty.
    if Decimal(stock):
        InventoryMovementFactory(variant=variant, quantity_change=Decimal(stock))
    return variant


def _store_with(variant, quantity: str) -> DbCartStore:
    user = UserFactory()
    cart = CartFactory(user=user)
    CartItemFactory(cart=cart, variant=variant, quantity=Decimal(quantity))
    return DbCartStore(user)


def _set_settings(
    delivery_fee: str = "45.00",
    threshold: str = "800.00",
    minimum: str = "150.00",
) -> None:
    row = ShopSettings.load()
    row.delivery_fee = Decimal(delivery_fee)
    row.free_delivery_threshold = Decimal(threshold)
    row.minimum_order_value = Decimal(minimum)
    row.save(
        update_fields=[
            "delivery_fee",
            "free_delivery_threshold",
            "minimum_order_value",
            "updated_at",
        ]
    )


# ---------------------------------------------------------------------------
# Line maths
# ---------------------------------------------------------------------------
def test_line_total_is_price_times_quantity_rounded_half_up() -> None:
    variant = _variant(price="19.99")

    summary = build_cart(_store_with(variant, "3"))

    assert summary.lines[0].line_total == Decimal("59.97")


def test_one_gram_of_a_priced_item_rounds_to_the_paisa() -> None:
    variant = _variant(price="10.00", loose=True)

    summary = build_cart(_store_with(variant, "0.001"))

    assert summary.lines[0].line_total == Decimal("0.01")


def test_a_half_paisa_rounds_up() -> None:
    variant = _variant(price="10.05", loose=True)

    summary = build_cart(_store_with(variant, "0.500"))

    assert summary.lines[0].line_total == Decimal("5.03")


def test_savings_are_mrp_total_minus_line_total() -> None:
    variant = _variant(price="80.00", mrp="100.00")

    summary = build_cart(_store_with(variant, "2"))

    line = summary.lines[0]
    assert line.mrp_total == Decimal("200.00")
    assert line.line_total == Decimal("160.00")
    assert line.savings == Decimal("40.00")


def test_money_values_are_decimals_with_two_places() -> None:
    variant = _variant(price="80.00", mrp="100.00")

    summary = build_cart(_store_with(variant, "2"))

    line = summary.lines[0]
    money = [
        line.unit_price,
        line.line_total,
        line.mrp_total,
        line.savings,
        summary.item_total,
        summary.delivery_fee,
        summary.grand_total,
        summary.savings_total,
        summary.minimum_order_value,
        summary.amount_short_of_minimum,
        summary.amount_to_free_delivery,
    ]
    for value in money:
        assert isinstance(value, Decimal)
        assert value.as_tuple().exponent == -2


# ---------------------------------------------------------------------------
# Delivery fee, threshold and minimum
# ---------------------------------------------------------------------------
def test_fee_is_charged_below_the_free_delivery_threshold() -> None:
    _set_settings()
    variant = _variant(price="700.00")

    summary = build_cart(_store_with(variant, "1"))

    assert summary.item_total == Decimal("700.00")
    assert summary.delivery_fee == Decimal("45.00")


def test_fee_is_zero_at_exactly_the_threshold() -> None:
    _set_settings()
    variant = _variant(price="800.00")

    summary = build_cart(_store_with(variant, "1"))

    assert summary.free_delivery_applies is True
    assert summary.delivery_fee == Decimal("0.00")


def test_fee_is_zero_above_the_threshold() -> None:
    _set_settings()
    variant = _variant(price="900.00")

    summary = build_cart(_store_with(variant, "1"))

    assert summary.free_delivery_applies is True
    assert summary.delivery_fee == Decimal("0.00")


def test_fee_is_charged_one_paisa_below_the_threshold() -> None:
    _set_settings()
    variant = _variant(price="799.99")

    summary = build_cart(_store_with(variant, "1"))

    assert summary.free_delivery_applies is False
    assert summary.delivery_fee == Decimal("45.00")


def test_amount_to_free_delivery_counts_down_and_stops_at_zero() -> None:
    _set_settings()
    variant = _variant(price="100.00")
    user = UserFactory()
    store = DbCartStore(user)
    add_item(store, variant, 3)

    short = build_cart(store)

    assert short.item_total == Decimal("300.00")
    assert short.amount_to_free_delivery == Decimal("500.00")

    set_item_quantity(store, variant, 9)
    far_enough = build_cart(store)

    assert far_enough.item_total == Decimal("900.00")
    assert far_enough.amount_to_free_delivery == Decimal("0.00")


def test_minimum_order_is_met_at_exactly_the_minimum() -> None:
    _set_settings()
    variant = _variant(price="150.00")

    summary = build_cart(_store_with(variant, "1"))

    assert summary.minimum_order_met is True
    assert summary.amount_short_of_minimum == Decimal("0.00")


def test_minimum_order_is_not_met_one_paisa_below() -> None:
    _set_settings()
    variant = _variant(price="149.99")

    summary = build_cart(_store_with(variant, "1"))

    assert summary.minimum_order_met is False
    assert summary.amount_short_of_minimum == Decimal("0.01")


def test_amount_short_of_the_minimum_is_reported() -> None:
    _set_settings()
    variant = _variant(price="100.00")

    summary = build_cart(_store_with(variant, "1"))

    assert summary.amount_short_of_minimum == Decimal("50.00")


def test_delivery_fee_comes_from_shop_settings() -> None:
    _set_settings()
    variant = _variant(price="300.00")

    summary = build_cart(_store_with(variant, "1"))

    # The model default is 30.00, so 45.00 proves the settings row was read.
    assert summary.delivery_fee == Decimal("45.00")


def test_free_delivery_threshold_comes_from_shop_settings() -> None:
    _set_settings()
    variant = _variant(price="700.00")

    summary = build_cart(_store_with(variant, "1"))

    # The model default of 500.00 would make this free.
    assert summary.free_delivery_applies is False
    assert summary.delivery_fee == Decimal("45.00")


def test_minimum_order_value_comes_from_shop_settings() -> None:
    _set_settings()
    variant = _variant(price="160.00")

    summary = build_cart(_store_with(variant, "1"))

    # The model default of 200.00 would leave the minimum unmet.
    assert summary.minimum_order_value == Decimal("150.00")
    assert summary.minimum_order_met is True


def test_a_settings_change_is_seen_on_the_next_build() -> None:
    _set_settings()
    variant = _variant(price="700.00")
    store = _store_with(variant, "1")
    first = build_cart(store)
    assert first.delivery_fee == Decimal("45.00")

    _set_settings(delivery_fee="60.00")
    second = build_cart(store)

    assert second.delivery_fee == Decimal("60.00")


# ---------------------------------------------------------------------------
# Empty and problem lines
# ---------------------------------------------------------------------------
def test_an_empty_cart_has_zero_totals_and_no_fee() -> None:
    summary = build_cart(DbCartStore(UserFactory()))

    assert summary.lines == []
    assert summary.item_total == Decimal("0.00")
    assert summary.delivery_fee == Decimal("0.00")
    assert summary.grand_total == Decimal("0.00")
    assert summary.free_delivery_applies is False
    assert summary.minimum_order_met is False
    assert summary.has_problem_lines is False
    assert summary.checkout_ready is False


def test_a_line_over_the_available_amount_is_flagged_and_left_out_of_the_total() -> None:
    variant = _variant(price="100.00", stock="2.000")

    summary = build_cart(_store_with(variant, "5"))

    assert summary.lines[0].status == STATUS_NOT_ENOUGH_STOCK
    assert summary.lines[0].line_total == Decimal("500.00")
    assert summary.item_total == Decimal("0.00")


def test_an_out_of_stock_line_is_flagged() -> None:
    variant = _variant(stock="0.000")

    summary = build_cart(_store_with(variant, "1"))

    assert summary.lines[0].status == STATUS_OUT_OF_STOCK
    assert summary.item_total == Decimal("0.00")


def test_an_archived_variant_line_is_flagged() -> None:
    variant = ProductVariantFactory(is_active=False)

    summary = build_cart(_store_with(variant, "1"))

    assert summary.lines[0].status == STATUS_UNAVAILABLE


def test_a_fractional_quantity_on_a_packet_variant_is_flagged_bad_quantity() -> None:
    variant = _variant()

    summary = build_cart(_store_with(variant, "1.500"))

    assert summary.lines[0].status == STATUS_BAD_QUANTITY


def test_a_quantity_off_the_step_after_the_step_changed_is_flagged() -> None:
    variant = _variant(loose=True, step="0.001", stock="10.000")
    store = _store_with(variant, "1.550")
    variant.quantity_step = Decimal("0.100")
    variant.save(update_fields=["quantity_step"])

    summary = build_cart(store)

    assert summary.lines[0].status == STATUS_BAD_QUANTITY


def test_problem_lines_block_checkout_ready() -> None:
    _set_settings()
    ok_variant = _variant(price="300.00", stock="10.000")
    problem_variant = _variant(price="100.00", stock="0.000")
    user = UserFactory()
    cart = CartFactory(user=user)
    CartItemFactory(cart=cart, variant=ok_variant, quantity=Decimal("1.000"))
    CartItemFactory(cart=cart, variant=problem_variant, quantity=Decimal("1.000"))

    summary = build_cart(DbCartStore(user))

    assert summary.minimum_order_met is True
    assert summary.has_problem_lines is True
    assert summary.checkout_ready is False


def test_checkout_ready_when_all_lines_are_ok_and_the_minimum_is_met() -> None:
    _set_settings()
    variant = _variant(price="300.00", stock="10.000")

    summary = build_cart(_store_with(variant, "1"))

    assert summary.has_problem_lines is False
    assert summary.minimum_order_met is True
    assert summary.checkout_ready is True


def test_grand_total_is_item_total_plus_fee() -> None:
    _set_settings()
    variant = _variant(price="700.00")

    summary = build_cart(_store_with(variant, "1"))

    assert summary.grand_total == summary.item_total + summary.delivery_fee
    assert summary.grand_total == Decimal("745.00")


# ---------------------------------------------------------------------------
# Order and query count
# ---------------------------------------------------------------------------
def test_lines_keep_the_order_they_were_added_in_the_db_cart() -> None:
    first = _variant()
    second = _variant()
    third = _variant()
    user = UserFactory()
    store = DbCartStore(user)
    add_item(store, first, 1)
    add_item(store, second, 1)
    add_item(store, third, 1)

    summary = build_cart(store)

    assert [line.variant.pk for line in summary.lines] == [first.pk, second.pk, third.pk]


def test_lines_keep_the_order_they_were_added_in_the_session_cart() -> None:
    first = _variant()
    second = _variant()
    third = _variant()
    session = SessionStore()
    store = SessionCartStore(session)
    add_item(store, first, 1)
    add_item(store, second, 1)
    add_item(store, third, 1)

    summary = build_cart(store)

    assert [line.variant.pk for line in summary.lines] == [first.pk, second.pk, third.pk]


def test_query_count_does_not_grow_with_the_number_of_lines() -> None:
    user = UserFactory()
    store = DbCartStore(user)
    add_item(store, _variant(), 1)
    # Warm the settings cache, so only the cart, stock and category reads count.
    get_shop_settings()

    with CaptureQueriesContext(connection) as one:
        build_cart(store)
    add_item(store, _variant(), 1)
    add_item(store, _variant(), 1)
    with CaptureQueriesContext(connection) as three:
        build_cart(store)

    assert len(one.captured_queries) == len(three.captured_queries)


def test_the_same_items_give_the_same_totals_in_both_stores() -> None:
    variant = _variant(price="80.00", mrp="100.00", stock="10.000")
    db_summary = build_cart(_store_with(variant, "2"))

    session = SessionStore()
    session["guest_cart"] = {str(variant.pk): "2.000"}
    session_summary = build_cart(SessionCartStore(session))

    assert db_summary.item_total == session_summary.item_total
    assert db_summary.delivery_fee == session_summary.delivery_fee
    assert db_summary.grand_total == session_summary.grand_total
    assert db_summary.savings_total == session_summary.savings_total
    assert db_summary.checkout_ready == session_summary.checkout_ready
    assert len(db_summary.lines) == len(session_summary.lines) == 1
