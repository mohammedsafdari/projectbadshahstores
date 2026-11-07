"""Tests for the order state machine table (D10, D64)."""

from __future__ import annotations

from apps.orders.models import Order
from apps.orders.state import (
    ALLOWED_TRANSITIONS,
    CANCELLED,
    COD_ONLY_BLOCKED,
    CONFIRMED,
    DELIVERED,
    OUT_FOR_DELIVERY,
    PACKING,
    PAID,
    PARTIALLY_REFUNDED,
    PAYMENT_FAILED,
    PAYMENT_PENDING,
    PLACED,
    REFUND_PENDING,
    REFUNDED,
    can_transition,
)


def test_every_status_has_a_transition_entry() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(Order.Status.values)


def test_every_transition_target_is_a_known_status() -> None:
    targets = [target for moves in ALLOWED_TRANSITIONS.values() for target in moves]
    assert targets
    assert set(targets) <= set(Order.Status.values)


def test_the_transition_table_is_exactly_this() -> None:
    # The literal copy is deliberately independent of state.py, so any added or
    # removed edge fails this test.
    expected = {
        "PLACED": ("PAYMENT_PENDING", "CONFIRMED", "CANCELLED"),
        "PAYMENT_PENDING": ("PAID", "PAYMENT_FAILED", "CANCELLED"),
        "PAYMENT_FAILED": ("PAYMENT_PENDING", "CANCELLED"),
        "PAID": ("CONFIRMED", "REFUND_PENDING"),
        "CONFIRMED": ("PACKING", "CANCELLED"),
        "PACKING": ("OUT_FOR_DELIVERY", "CANCELLED"),
        "OUT_FOR_DELIVERY": ("DELIVERED", "CANCELLED"),
        "DELIVERED": (),
        "CANCELLED": (),
        "REFUND_PENDING": ("REFUNDED", "PARTIALLY_REFUNDED"),
        "REFUNDED": (),
        "PARTIALLY_REFUNDED": (),
    }
    # Comparing sets ignores the order inside each tuple, which is not a rule.
    actual = {status: set(moves) for status, moves in ALLOWED_TRANSITIONS.items()}
    wanted = {status: set(moves) for status, moves in expected.items()}
    assert actual == wanted


def test_placed_can_move_to_payment_pending() -> None:
    assert can_transition(PLACED, PAYMENT_PENDING)


def test_placed_can_be_confirmed() -> None:
    assert can_transition(PLACED, CONFIRMED)


def test_placed_can_be_cancelled() -> None:
    assert can_transition(PLACED, CANCELLED)


def test_placed_cannot_skip_to_packing() -> None:
    assert not can_transition(PLACED, PACKING)


def test_payment_pending_can_be_paid() -> None:
    assert can_transition(PAYMENT_PENDING, PAID)


def test_payment_pending_can_fail() -> None:
    assert can_transition(PAYMENT_PENDING, PAYMENT_FAILED)


def test_payment_failed_can_retry() -> None:
    assert can_transition(PAYMENT_FAILED, PAYMENT_PENDING)


def test_paid_can_be_confirmed() -> None:
    assert can_transition(PAID, CONFIRMED)


def test_paid_can_move_to_refund_pending() -> None:
    assert can_transition(PAID, REFUND_PENDING)


def test_confirmed_can_move_to_packing() -> None:
    assert can_transition(CONFIRMED, PACKING)


def test_confirmed_can_be_cancelled() -> None:
    assert can_transition(CONFIRMED, CANCELLED)


def test_packing_can_move_to_out_for_delivery() -> None:
    assert can_transition(PACKING, OUT_FOR_DELIVERY)


def test_packing_can_be_cancelled() -> None:
    assert can_transition(PACKING, CANCELLED)


def test_packing_cannot_move_back_to_confirmed() -> None:
    assert not can_transition(PACKING, CONFIRMED)


def test_out_for_delivery_can_be_delivered() -> None:
    assert can_transition(OUT_FOR_DELIVERY, DELIVERED)


def test_out_for_delivery_can_be_cancelled() -> None:
    assert can_transition(OUT_FOR_DELIVERY, CANCELLED)


def test_refund_pending_can_be_refunded() -> None:
    assert can_transition(REFUND_PENDING, REFUNDED)


def test_refund_pending_can_be_partially_refunded() -> None:
    assert can_transition(REFUND_PENDING, PARTIALLY_REFUNDED)


def test_delivered_is_terminal() -> None:
    assert ALLOWED_TRANSITIONS[DELIVERED] == ()


def test_cancelled_is_terminal() -> None:
    assert ALLOWED_TRANSITIONS[CANCELLED] == ()


def test_refunded_is_terminal() -> None:
    assert ALLOWED_TRANSITIONS[REFUNDED] == ()


def test_partially_refunded_is_terminal() -> None:
    assert ALLOWED_TRANSITIONS[PARTIALLY_REFUNDED] == ()


def test_an_unknown_from_status_has_no_moves() -> None:
    assert not can_transition("BOGUS", PLACED)


def test_an_unknown_to_status_is_refused() -> None:
    assert not can_transition(PLACED, "BOGUS")


def test_cod_only_blocked_lists_every_payment_and_refund_status() -> None:
    assert set(COD_ONLY_BLOCKED) == {
        PAYMENT_PENDING,
        PAID,
        PAYMENT_FAILED,
        REFUND_PENDING,
        REFUNDED,
        PARTIALLY_REFUNDED,
    }
