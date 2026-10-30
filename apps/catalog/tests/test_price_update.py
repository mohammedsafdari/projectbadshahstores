"""Tests for the bulk price update (Slice 21b).

``plan_price_update`` reads once and writes nothing; ``apply_price_update`` locks,
plans again and writes every selected row or none, with one audit row per change.
The concurrency test at the end needs ``transaction=True`` because it starts real
threads, each with its own database connection.
"""

from __future__ import annotations

import threading
import time
from decimal import Decimal
from typing import Any

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from apps.catalog.audit import VARIANT_UPDATE_ACTION
from apps.catalog.models import ProductVariant
from apps.catalog.price_update import (
    AMOUNT,
    MAX_VARIANTS,
    PERCENT,
    SET,
    PlanChanged,
    PriceSpec,
    PriceUpdateRejected,
    apply_price_update,
    plan_price_update,
)
from apps.catalog.tests.factories import ProductVariantFactory
from apps.core.models import AuditLog
from apps.inventory.models import InventoryMovement
from tests.factories import UserFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_spec(
    method: str, value: str, *, field: str = "selling_price", step: str = "1"
) -> PriceSpec:
    """A spec built from text, so the tests never write a float by accident."""
    return PriceSpec(field=field, method=method, value=Decimal(value), step=Decimal(step))


def make_variant(**changes: Any) -> ProductVariant:
    """One saved variant with a small, known price: MRP 100.00, selling price 10.00."""
    fields: dict[str, Any] = {
        "mrp": Decimal("100.00"),
        "selling_price": Decimal("10.00"),
        "cost_price": None,
    }
    # One dict, then the overrides: naming a key here and again in ** would pass it twice.
    fields.update(changes)
    return ProductVariantFactory(**fields)


# ---------------------------------------------------------------------------
# The three methods
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_percent_up() -> None:
    variant = make_variant()

    plan = plan_price_update([variant.pk], make_spec(PERCENT, "20"))

    assert plan.ok
    row = plan.rows[0]
    assert row.sku_code == variant.sku_code
    assert row.old == Decimal("10.00")
    assert row.new == Decimal("12.00")


@pytest.mark.django_db
def test_percent_down() -> None:
    variant = make_variant()

    plan = plan_price_update([variant.pk], make_spec(PERCENT, "-10"))

    # 10.00 * (1 - 10/100) = 9.00; a dropped minus sign would give 11.00.
    assert plan.rows[0].new == Decimal("9.00")


@pytest.mark.django_db
def test_half_up_at_a_step_of_one() -> None:
    variant = make_variant()

    plan = plan_price_update([variant.pk], make_spec(PERCENT, "5"))

    # 10.00 * 1.05 = 10.50, which is exactly half a rupee. Half-up makes it 11.00;
    # half-even or truncating would give 10.00.
    assert plan.rows[0].new == Decimal("11.00")


@pytest.mark.django_db
def test_half_up_at_a_step_of_half() -> None:
    variant = make_variant()

    plan = plan_price_update([variant.pk], make_spec(PERCENT, "2.5", step="0.50"))

    # 10.00 * 1.025 = 10.25, exactly half of a 0.50 step, so it rounds up.
    assert plan.rows[0].new == Decimal("10.50")


@pytest.mark.django_db
def test_set_exact_is_not_rounded() -> None:
    variant = make_variant()

    plan = plan_price_update([variant.pk], make_spec(SET, "12.34"))

    # The default step is 1.00; "set exact" ignores it, so the paise survive.
    assert plan.rows[0].new == Decimal("12.34")


@pytest.mark.django_db
def test_amount_change_adds_and_subtracts_rupees() -> None:
    variant = make_variant()

    up = plan_price_update([variant.pk], make_spec(AMOUNT, "2.50", step="0.01"))
    assert up.rows[0].new == Decimal("12.50")

    down = plan_price_update([variant.pk], make_spec(AMOUNT, "-2.50", step="0.01"))
    assert down.rows[0].new == Decimal("7.50")


# ---------------------------------------------------------------------------
# Row rules
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_selling_price_above_mrp_is_a_problem() -> None:
    variant = make_variant()  # MRP 100.00, selling price 10.00

    plan = plan_price_update([variant.pk], make_spec(PERCENT, "1000"))

    # 10.00 * 11 = 110.00, above the stored MRP.
    assert not plan.ok
    assert plan.rows == []
    assert plan.problems[0].sku_code == variant.sku_code
    assert "above the stored MRP" in plan.problems[0].message


@pytest.mark.django_db
def test_an_mrp_below_the_stored_selling_price_is_a_problem() -> None:
    variant = make_variant(mrp=Decimal("100.00"), selling_price=Decimal("90.00"))

    plan = plan_price_update([variant.pk], make_spec(PERCENT, "-20", field="mrp"))

    # 100.00 * 0.80 = 80.00, below the stored selling price of 90.00.
    assert not plan.ok
    assert "below the stored selling price" in plan.problems[0].message


@pytest.mark.django_db
def test_a_new_selling_price_must_be_more_than_zero() -> None:
    variant = make_variant()

    plan = plan_price_update([variant.pk], make_spec(SET, "0"))

    assert not plan.ok
    assert plan.problems[0].sku_code == variant.sku_code
    assert "more than 0" in plan.problems[0].message


@pytest.mark.django_db
def test_a_new_cost_price_may_be_zero_but_not_negative() -> None:
    variant = make_variant(cost_price=Decimal("4.00"))

    zero = plan_price_update([variant.pk], make_spec(SET, "0", field="cost_price"))
    assert zero.ok
    assert zero.rows[0].new == Decimal("0.00")

    negative = plan_price_update([variant.pk], make_spec(SET, "-1", field="cost_price"))
    assert not negative.ok
    assert "0 or more" in negative.problems[0].message


@pytest.mark.django_db
def test_a_new_price_that_does_not_fit_ten_digits_is_a_problem() -> None:
    variant = make_variant()

    plan = plan_price_update([variant.pk], make_spec(SET, "100000000"))

    # 100,000,000 needs nine digits before the decimal point; the field holds eight.
    assert not plan.ok
    assert "does not fit 10 digits" in plan.problems[0].message


@pytest.mark.django_db
def test_a_blank_cost_price_is_skipped_under_percent() -> None:
    actor = UserFactory()
    variant = make_variant(cost_price=None)

    plan = plan_price_update([variant.pk], make_spec(PERCENT, "10", field="cost_price"))

    # A percentage of nothing cannot be worked out, so the row is skipped, not blocked.
    assert plan.ok
    assert plan.rows == []
    assert len(plan.skipped) == 1
    assert plan.skipped[0].sku_code == variant.sku_code
    assert "blank" in plan.skipped[0].reason

    changed = apply_price_update(
        [variant.pk], make_spec(PERCENT, "10", field="cost_price"), actor=actor
    )
    assert changed == 0
    variant.refresh_from_db()
    assert variant.cost_price is None
    assert AuditLog.objects.count() == 0


@pytest.mark.django_db
def test_a_blank_cost_price_can_be_set() -> None:
    actor = UserFactory()
    variant = make_variant(cost_price=None)

    plan = plan_price_update([variant.pk], make_spec(SET, "25.50", field="cost_price"))
    assert len(plan.rows) == 1
    assert plan.rows[0].old is None
    assert plan.rows[0].new == Decimal("25.50")

    changed = apply_price_update(
        [variant.pk], make_spec(SET, "25.50", field="cost_price"), actor=actor
    )
    assert changed == 1
    variant.refresh_from_db()
    assert variant.cost_price == Decimal("25.50")
    assert AuditLog.objects.get().changes == {"cost_price": [None, "25.50"]}


@pytest.mark.django_db
def test_a_row_whose_price_does_not_change_is_not_listed() -> None:
    variant = make_variant()

    plan = plan_price_update([variant.pk], make_spec(PERCENT, "0"))

    # 10.00 with a 0% change is still 10.00: not a change, not a skip, not a problem.
    assert plan.ok
    assert plan.rows == []
    assert plan.skipped == []


@pytest.mark.django_db
def test_a_change_of_more_than_fifty_percent_warns_but_does_not_block() -> None:
    variant = make_variant()

    plan = plan_price_update([variant.pk], make_spec(PERCENT, "100"))

    assert plan.ok
    assert len(plan.rows) == 1
    assert len(plan.warnings) == 1
    assert variant.sku_code in plan.warnings[0]


@pytest.mark.django_db
def test_the_spec_itself_is_checked() -> None:
    variant = make_variant()

    too_precise = plan_price_update([variant.pk], make_spec(PERCENT, "2.555"))
    assert not too_precise.ok
    assert "2 decimal places" in too_precise.problems[0].message

    wiped_out = plan_price_update([variant.pk], make_spec(PERCENT, "-100"))
    assert not wiped_out.ok
    assert "zero or negative" in wiped_out.problems[0].message


# ---------------------------------------------------------------------------
# The run limit
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_more_than_max_variants_is_refused_without_reading_rows() -> None:
    # The ids do not exist, which is the point: the refusal must come before the
    # query, so this test needs no stored variants at all.
    pks = list(range(1, MAX_VARIANTS + 2))
    assert len(pks) == MAX_VARIANTS + 1

    with CaptureQueriesContext(connection) as queries:
        plan = plan_price_update(pks, make_spec(PERCENT, "10"))

    assert not plan.ok
    assert f"at most {MAX_VARIANTS}" in plan.problems[0].message.lower()
    assert plan.rows == []
    assert len(queries.captured_queries) == 0

    with pytest.raises(PriceUpdateRejected):
        apply_price_update(pks, make_spec(PERCENT, "10"), actor=None)


# ---------------------------------------------------------------------------
# The plan only reads
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_the_plan_reads_once_and_writes_nothing() -> None:
    first = make_variant()
    second = make_variant()

    with CaptureQueriesContext(connection) as queries:
        plan = plan_price_update([first.pk, second.pk], make_spec(PERCENT, "10"))

    assert len(plan.rows) == 2
    # One SELECT for both variants, in pk order, and nothing that writes.
    assert len(queries.captured_queries) == 1
    assert queries.captured_queries[0]["sql"].startswith("SELECT")


# ---------------------------------------------------------------------------
# What apply writes
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_apply_writes_one_field_one_audit_row_and_no_stock() -> None:
    actor = UserFactory()
    first = make_variant()
    second = make_variant()
    untouched = make_variant()
    first.refresh_from_db()  # read the timestamp the database really stores
    second.refresh_from_db()
    before = (first.updated_at, second.updated_at)

    changed = apply_price_update([first.pk, second.pk], make_spec(PERCENT, "10"), actor=actor)

    assert changed == 2
    first.refresh_from_db()
    second.refresh_from_db()
    untouched.refresh_from_db()
    assert first.selling_price == Decimal("11.00")
    assert second.selling_price == Decimal("11.00")
    assert untouched.selling_price == Decimal("10.00")

    # save(update_fields=...) must name updated_at too, or the timestamp would stay put.
    assert first.updated_at > before[0]
    assert second.updated_at > before[1]

    # Nothing else on the row changed: update_fields names the price and the timestamp.
    assert first.mrp == Decimal("100.00")
    assert first.cost_price is None
    assert first.gst_rate_percent == Decimal("5.00")
    assert first.is_active is True
    assert first.barcode is None
    assert first.pack_size == Decimal("1.000")

    audit_rows = list(AuditLog.objects.all())
    assert len(audit_rows) == 2
    assert {row.target_id for row in audit_rows} == {str(first.pk), str(second.pk)}
    for row in audit_rows:
        assert row.action == VARIANT_UPDATE_ACTION
        assert row.actor_id == actor.pk
        assert row.note == "Bulk price update: selling price +10% (round to 1)"
        assert row.changes == {"selling_price": ["10.00", "11.00"]}

    # A price change never writes the stock ledger.
    assert InventoryMovement.objects.count() == 0


@pytest.mark.django_db
def test_archived_variants_are_included() -> None:
    actor = UserFactory()
    variant = make_variant(is_active=False)

    plan = plan_price_update([variant.pk], make_spec(PERCENT, "10"))
    assert len(plan.rows) == 1

    assert apply_price_update([variant.pk], make_spec(PERCENT, "10"), actor=actor) == 1
    variant.refresh_from_db()
    assert variant.selling_price == Decimal("11.00")
    assert variant.is_active is False


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_one_problem_writes_nothing() -> None:
    actor = UserFactory()
    good = make_variant()
    bad = make_variant(mrp=Decimal("100.00"), selling_price=Decimal("90.00"))

    with pytest.raises(PriceUpdateRejected) as excinfo:
        # MRP 50.00 is fine for "good" (selling price 10.00) but below the stored
        # selling price of "bad".
        apply_price_update([good.pk, bad.pk], make_spec(SET, "50", field="mrp"), actor=actor)

    assert [problem.sku_code for problem in excinfo.value.problems] == [bad.sku_code]
    good.refresh_from_db()
    bad.refresh_from_db()
    assert good.mrp == Decimal("100.00")
    assert bad.mrp == Decimal("100.00")
    assert AuditLog.objects.count() == 0


@pytest.mark.django_db
def test_stale_fingerprint_raises_plan_changed_and_writes_nothing() -> None:
    actor = UserFactory()
    variant = make_variant()
    seen = plan_price_update([variant.pk], make_spec(PERCENT, "10")).fingerprint()

    # Somebody edits the variant after the preview was shown.
    variant.selling_price = Decimal("20.00")
    variant.save()

    with pytest.raises(PlanChanged):
        apply_price_update(
            [variant.pk],
            make_spec(PERCENT, "10"),
            actor=actor,
            expected_fingerprint=seen,
        )

    # The other person's 20.00 survives, and no audit row was written.
    variant.refresh_from_db()
    assert variant.selling_price == Decimal("20.00")
    assert AuditLog.objects.count() == 0


@pytest.mark.django_db
def test_a_change_to_another_field_also_invalidates_the_preview() -> None:
    actor = UserFactory()
    variant = make_variant()
    seen = plan_price_update([variant.pk], make_spec(PERCENT, "10")).fingerprint()

    # The prices in the plan stay 10.00 -> 11.00, so only updated_at can tell the
    # fresh plan apart from the preview. A tax change is enough to move it.
    variant.gst_rate_percent = Decimal("12.00")
    variant.save()

    with pytest.raises(PlanChanged):
        apply_price_update(
            [variant.pk],
            make_spec(PERCENT, "10"),
            actor=actor,
            expected_fingerprint=seen,
        )

    variant.refresh_from_db()
    assert variant.selling_price == Decimal("10.00")
    assert AuditLog.objects.count() == 0


# ---------------------------------------------------------------------------
# The lock: apply must wait for a transaction that already holds the variant
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_apply_waits_for_a_lock_held_by_another_transaction() -> None:
    actor = UserFactory()
    variant = make_variant()
    variant_pk = variant.pk
    spec = make_spec(PERCENT, "10")

    locked = threading.Event()
    outcome: list[int] = []
    errors: list[BaseException] = []

    def hold_the_lock() -> None:
        """Set the selling price to 20.00 inside a transaction that holds the row lock."""
        try:
            with transaction.atomic():
                row = ProductVariant.objects.select_for_update().get(pk=variant_pk)
                locked.set()  # tell the other thread the lock is held
                time.sleep(0.5)  # keep it for a moment, so the other thread must wait
                row.selling_price = Decimal("20.00")
                row.save(update_fields=["selling_price", "updated_at"])
            # Leaving the ``with`` block commits, which releases the lock.
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()  # this thread's own connection, or the test DB cannot drop

    def apply_while_locked() -> None:
        """Run the update: it must block on the lock, then change 20.00 to 22.00."""
        try:
            assert locked.wait(timeout=10)
            outcome.append(apply_price_update([variant_pk], spec, actor=actor))
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    first = threading.Thread(target=hold_the_lock)
    second = threading.Thread(target=apply_while_locked)
    first.start()
    second.start()
    first.join(timeout=30)
    second.join(timeout=30)

    assert not errors, errors
    assert outcome == [1]

    # If apply_price_update did not lock, it would have planned 10.00 -> 11.00 before
    # the other transaction committed, so the audit row would show the old value 10.00
    # and the stored price would be 11.00.
    row = AuditLog.objects.get()
    assert row.action == VARIANT_UPDATE_ACTION
    assert row.changes == {"selling_price": ["20.00", "22.00"]}
    assert ProductVariant.objects.get(pk=variant_pk).selling_price == Decimal("22.00")
