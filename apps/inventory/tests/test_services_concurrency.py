"""Concurrency tests for the inventory services (Slice 16d).

These tests start real threads. Each thread gets its OWN database connection,
so it can only see rows that are committed in that thread; it cannot see the
rows sitting inside the normal pytest-django test transaction.

Every test below therefore:
- uses @pytest.mark.django_db(transaction=True) on the test function (not a
  module-level pytestmark), which lets the test itself use transactions but
  allows threads to commit their own;
- creates all of its data in the main thread BEFORE starting the threads, so
  every thread starts from the same committed base;
- asserts on invariants (counts, sums, final on-hand) and never on timing or
  order.
"""

from __future__ import annotations

import concurrent.futures
import threading
from decimal import Decimal

import pytest
from django.db import connection

from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.models import InventoryMovement
from apps.inventory.services import (
    InsufficientStockError,
    InventoryError,
    get_on_hand,
    receive_stock,
    record_damage,
)


def _run_concurrently(worker, count):
    """Run `worker(index)` in `count` threads, all starting at the same moment.

    `threading.Barrier(count)` lets us release all of the threads together: each
    thread calls `barrier.wait()`, which blocks until `count` threads have called
    it, then releases them at once. That is much more reproducible than sleeping.

    The tricky part for Django: a thread spawned outside the main test transaction
    gets its own database connection, so it cannot see the uncommitted rows that
    live in the main thread's transaction. That is why the data is created in the
    main thread BEFORE the threads start, and why each thread commits its own work
    (the services already run inside `transaction.atomic()`).

    Finally, every thread MUST close its own connection in a `finally:` block.
    Otherwise the connection stays open and Django's test teardown cannot drop the
    test database. The `close()` call here only ends the thread's connection; it
    does not affect the main thread.
    """
    barrier = threading.Barrier(count)

    def _target(index):
        try:
            barrier.wait(timeout=10)
            result = worker(index)
            return ("ok", result)
        except InventoryError as exc:
            return ("error", exc)
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(_target, idx) for idx in range(count)]
        results = [future.result() for future in futures]

    return results


# ---------------------------------------------------------------------------
# Packet variant: damage race with a hard on-hand limit
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_packet_damage_race_uses_up_stock_and_waits_rest() -> None:
    # 8 threads race to damage 1 unit each; on-hand is only 5, so 3 must fail.
    variant = ProductVariantFactory(is_sold_loose=False)
    InventoryMovement.objects.create(
        variant=variant,
        movement_type=InventoryMovement.MovementType.PURCHASE_RECEIVED,
        quantity_change=Decimal("5"),
    )

    def worker(index):
        return record_damage(variant=variant, quantity=1, reason="race")

    outcomes = _run_concurrently(worker, count=8)

    ok_outcomes = [outcome for outcome in outcomes if outcome[0] == "ok"]
    error_outcomes = [outcome for outcome in outcomes if outcome[0] == "error"]

    assert len(ok_outcomes) == 5
    assert len(error_outcomes) == 3
    assert all(isinstance(outcome[1], InsufficientStockError) for outcome in error_outcomes)

    assert get_on_hand(variant) == Decimal("0")
    assert (
        InventoryMovement.objects.filter(
            variant=variant,
            movement_type=InventoryMovement.MovementType.DAMAGE,
        ).count()
        == 5
    )


# ---------------------------------------------------------------------------
# Loose variant: mixed receive and damage, asserted on invariants only
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_loose_variant_receive_and_damage_concurrently_keeps_invariants() -> None:
    # 10 workers: 4 receive 0.500 each, 6 damage 0.400 each. We do not assert
    # *which* threads succeed, only on the final totals.
    #
    # Because damage is rejected when on-hand would go below 0, the final on-hand
    # is deterministic from the number of successful damage calls, which is what
    # we derive from the outcomes.
    variant = ProductVariantFactory(
        is_sold_loose=True,
        unit=ProductVariant.Unit.KG,
        pack_size=Decimal("1"),
    )
    InventoryMovement.objects.create(
        variant=variant,
        movement_type=InventoryMovement.MovementType.PURCHASE_RECEIVED,
        quantity_change=Decimal("1.000"),
    )

    def worker(index):
        if index < 4:
            return receive_stock(variant=variant, quantity=Decimal("0.500"))
        return record_damage(variant=variant, quantity=Decimal("0.400"), reason="race")

    outcomes = _run_concurrently(worker, count=10)

    successful_damages = sum(
        1
        for outcome in outcomes
        if outcome[0] == "error" and not isinstance(outcome[1], InsufficientStockError)
    ) + sum(
        1
        for outcome in outcomes
        if outcome[0] == "ok" and not isinstance(outcome[1], InventoryMovement)
    )
    successful_damages = 0
    for status, value in outcomes:
        if status == "ok":
            movement = value
            assert isinstance(movement, InventoryMovement)
            if movement.movement_type == InventoryMovement.MovementType.DAMAGE:
                successful_damages += 1
        else:
            assert isinstance(value, InsufficientStockError)

    expected_on_hand = (
        Decimal("1.000")
        + (Decimal("4") * Decimal("0.500"))
        - (Decimal("0.400") * Decimal(str(successful_damages)))
    )
    assert expected_on_hand >= 0
    assert get_on_hand(variant) == expected_on_hand

    all_movements = InventoryMovement.objects.filter(variant=variant)
    total_moved = sum(movement.quantity_change for movement in all_movements)
    assert total_moved == get_on_hand(variant)

    errors = [outcome for outcome in outcomes if outcome[0] == "error"]
    assert all(isinstance(error[1], InsufficientStockError) for error in errors)
