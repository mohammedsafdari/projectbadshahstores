"""Concurrency tests for the inventory services (Slice 16d).

These tests start real threads. Each thread gets its OWN database connection,
so it can only see rows that are committed in that thread; it cannot see the
rows sitting inside the normal pytest-django test transaction.

Every test below therefore:
- uses @pytest.mark.django_db(transaction=True) on the test function (not a
  module-level pytestmark), so the data really is committed and visible to the
  threads;
- creates all of its data in the main thread BEFORE starting the threads, so
  every thread starts from the same committed base;
- asserts on counts and sums, never on timing or order.
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
    record_damage,
)


def _run_concurrently(worker, count):
    """Run `worker(index)` in `count` threads, all starting at the same moment.

    `threading.Barrier(count)` releases all of the threads together: each thread
    calls `barrier.wait()`, which blocks until `count` threads have called it,
    then lets them all go at once. That is far more reliable than sleeping.

    A thread gets its own database connection, so it cannot see uncommitted rows
    of the main thread. That is why the data is created BEFORE the threads start.
    Each service call commits its own transaction.

    Every thread MUST close its own connection in a `finally:` block. Otherwise
    the connection stays open and Django cannot drop the test database at the
    end. `close()` only ends this thread's connection.

    Returns a list of ("ok", result) or ("error", exception) tuples. Only
    InventoryError is turned into an outcome; any other exception propagates
    and fails the test.
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
# Loose variant: damage race with decimal quantities
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_loose_damage_race_allows_only_what_the_stock_covers() -> None:
    # 6 x 0.400 = 2.400 is more than the 1.000 in stock, so without the lock
    # more than 2 calls would get through.
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
        return record_damage(variant=variant, quantity=Decimal("0.400"), reason="race")

    outcomes = _run_concurrently(worker, count=6)

    ok_outcomes = [outcome for outcome in outcomes if outcome[0] == "ok"]
    error_outcomes = [outcome for outcome in outcomes if outcome[0] == "error"]

    assert len(ok_outcomes) == 2
    for _status, movement in ok_outcomes:
        assert isinstance(movement, InventoryMovement)
        assert movement.quantity_change == Decimal("-0.400")

    assert len(error_outcomes) == 4
    assert all(isinstance(outcome[1], InsufficientStockError) for outcome in error_outcomes)

    assert get_on_hand(variant) == Decimal("0.200")
    assert (
        InventoryMovement.objects.filter(
            variant=variant,
            movement_type=InventoryMovement.MovementType.DAMAGE,
        ).count()
        == 2
    )
