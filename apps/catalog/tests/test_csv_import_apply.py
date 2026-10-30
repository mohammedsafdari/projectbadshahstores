"""Tests for the CSV import apply step (Slice 20c, ADR D47).

The apply step writes products and variants inside one transaction, locks before it
plans, and audits every variant it creates or changes. The concurrency test at the end
needs ``transaction=True`` because it starts real threads, each with its own database
connection.
"""

from __future__ import annotations

import threading
import time
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, transaction

from apps.catalog.audit import VARIANT_CREATE_ACTION, VARIANT_UPDATE_ACTION
from apps.catalog.csv_import_apply import (
    ApplyResult,
    ImportRejected,
    PlanChanged,
    apply_import,
)
from apps.catalog.models import Product, ProductVariant
from apps.catalog.tests import test_csv_import_plan as plan_tests
from apps.catalog.tests.test_csv_import_parse import make_csv
from apps.catalog.tests.test_csv_import_plan import PRODUCT_SLUG, SKU, plan_for, stored_catalog
from apps.core.models import AuditLog
from apps.inventory.models import InventoryMovement
from tests.factories import UserFactory

# pytest finds a fixture by looking at this module's attributes, so an alias is enough
# to reuse the plan tests' ``catalog`` fixture. Importing the name directly would shadow
# it in every test signature below, which ruff reports as a redefinition (F811).
catalog = plan_tests.catalog


# ---------------------------------------------------------------------------
# What one good file does
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_creates_product_and_variant_with_one_create_audit_row(catalog) -> None:
    actor = UserFactory()
    result = apply_import(make_csv({}), actor=actor, filename="file.csv")

    assert result.created == 1
    assert result.unchanged == 0
    assert result.products_created == 1
    variant = ProductVariant.objects.get(sku_code=SKU)
    assert variant.product.slug == PRODUCT_SLUG

    row = AuditLog.objects.get()
    assert row.action == VARIANT_CREATE_ACTION
    assert row.actor_id == actor.pk
    assert row.target_type == "catalog.productvariant"
    assert row.target_id == str(variant.pk)
    assert row.note == "CSV import: file.csv"
    # A created row has no old values, so the pair starts with None.
    assert row.changes["selling_price"] == [None, "265.00"]

    # The import never writes the stock ledger (D47 point 8).
    assert InventoryMovement.objects.count() == 0


@pytest.mark.django_db
def test_selling_price_update_writes_one_update_audit_row(catalog) -> None:
    actor = UserFactory()
    stored_catalog(catalog)
    result = apply_import(make_csv({"selling_price": "250.00"}), actor=actor, filename="file.csv")

    assert result.updated == 1
    assert ProductVariant.objects.get(sku_code=SKU).selling_price == Decimal("250.00")
    row = AuditLog.objects.get()
    assert row.action == VARIANT_UPDATE_ACTION
    assert row.changes == {"selling_price": ["265.00", "250.00"]}


@pytest.mark.django_db
def test_unchanged_file_writes_no_audit_row_and_keeps_updated_at(catalog) -> None:
    actor = UserFactory()
    variant = stored_catalog(catalog)
    variant.refresh_from_db()  # read the timestamp the database really stores
    before = variant.updated_at

    result = apply_import(make_csv({}), actor=actor, filename="file.csv")

    assert result.unchanged == 1
    assert AuditLog.objects.count() == 0
    variant.refresh_from_db()
    assert variant.updated_at == before


@pytest.mark.django_db
def test_non_audited_field_saves_without_an_audit_row(catalog) -> None:
    actor = UserFactory()
    stored_catalog(catalog)
    result = apply_import(make_csv({"hsn_code": "1102"}), actor=actor, filename="file.csv")

    assert result.updated == 1
    assert ProductVariant.objects.get(sku_code=SKU).hsn_code == "1102"
    assert AuditLog.objects.count() == 0


@pytest.mark.django_db
def test_product_name_change_is_saved_without_an_audit_row(catalog) -> None:
    actor = UserFactory()
    stored_catalog(catalog)
    result = apply_import(
        make_csv({"product_name_en": "Chakki Atta Premium"}),
        actor=actor,
        filename="file.csv",
    )

    assert result.products_updated == 1
    assert result.updated == 1  # the row counts as updated even though its variant did not change
    assert Product.objects.get(slug=PRODUCT_SLUG).name_en == "Chakki Atta Premium"
    assert AuditLog.objects.count() == 0


# ---------------------------------------------------------------------------
# updated_at: save(update_fields=...) writes only the columns it is given
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_variant_update_moves_updated_at(catalog) -> None:
    actor = UserFactory()
    variant = stored_catalog(catalog)
    variant.refresh_from_db()  # read the timestamp the database really stores
    before = variant.updated_at

    apply_import(make_csv({"selling_price": "250.00"}), actor=actor, filename="file.csv")

    variant.refresh_from_db()
    # If the apply step dropped "updated_at" from update_fields, the stored
    # timestamp would still be ``before`` and this assertion would fail.
    assert variant.updated_at > before


@pytest.mark.django_db
def test_a_product_update_moves_the_product_updated_at(catalog) -> None:
    actor = UserFactory()
    product = stored_catalog(catalog).product
    product.refresh_from_db()
    before = product.updated_at

    apply_import(
        make_csv({"product_name_en": "Chakki Atta Premium"}),
        actor=actor,
        filename="file.csv",
    )

    product.refresh_from_db()
    assert product.updated_at > before


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_row_that_fails_while_writing_rolls_the_whole_file_back(catalog, monkeypatch) -> None:
    actor = UserFactory()
    original = ProductVariant.full_clean

    def failing_full_clean(self: ProductVariant, *args: Any, **kwargs: Any) -> None:
        # The first row is fine; the second one fails, as if its barcode clashed.
        if self.sku_code == "ATTA-1KG":
            raise ValidationError("forced failure")
        original(self, *args, **kwargs)

    monkeypatch.setattr(ProductVariant, "full_clean", failing_full_clean)

    with pytest.raises(ImportRejected) as excinfo:
        apply_import(
            make_csv(
                {"sku_code": "ATTA-5KG", "barcode": ""},
                {"sku_code": "ATTA-1KG", "barcode": ""},
            ),
            actor=actor,
            filename="file.csv",
        )

    # The problem names the row that failed: the second data row is line 3.
    assert excinfo.value.issues[0].line == 3
    # Nothing of the file survives, not even the product and the first variant.
    assert Product.objects.filter(slug=PRODUCT_SLUG).count() == 0
    assert ProductVariant.objects.count() == 0
    assert AuditLog.objects.count() == 0


@pytest.mark.django_db
def test_plan_errors_are_rejected_and_write_nothing(catalog) -> None:
    actor = UserFactory()
    with pytest.raises(ImportRejected) as excinfo:
        apply_import(
            make_csv({"brand_slug": "no-such-brand"}),
            actor=actor,
            filename="file.csv",
        )

    assert [issue.column for issue in excinfo.value.issues] == ["brand_slug"]
    assert Product.objects.count() == 0
    assert ProductVariant.objects.count() == 0
    assert AuditLog.objects.count() == 0


@pytest.mark.django_db
def test_stale_fingerprint_raises_plan_changed_and_writes_nothing(catalog) -> None:
    actor = UserFactory()
    variant = stored_catalog(catalog)
    seen = plan_for({"selling_price": "250.00"}).fingerprint()

    # Somebody edits the variant after the preview was shown.
    variant.selling_price = Decimal("260.00")
    variant.save()

    with pytest.raises(PlanChanged):
        apply_import(
            make_csv({"selling_price": "250.00"}),
            actor=actor,
            filename="file.csv",
            expected_fingerprint=seen,
        )

    variant.refresh_from_db()
    assert variant.selling_price == Decimal("260.00")
    assert AuditLog.objects.count() == 0


# ---------------------------------------------------------------------------
# The audit note
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_note_removes_control_characters(catalog) -> None:
    actor = UserFactory()
    apply_import(make_csv({}), actor=actor, filename="file\n\t.csv")

    assert AuditLog.objects.get().note == "CSV import: file.csv"


@pytest.mark.django_db
def test_note_is_cut_to_200_characters(catalog) -> None:
    actor = UserFactory()
    apply_import(make_csv({}), actor=actor, filename="a" * 300 + ".csv")

    note = AuditLog.objects.get().note
    assert len(note) == 200
    assert note.startswith("CSV import: aaa")


# ---------------------------------------------------------------------------
# The lock: apply must wait for a transaction that already holds the variant
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_apply_waits_for_a_lock_held_by_another_transaction(catalog) -> None:
    actor = UserFactory()
    variant = stored_catalog(catalog)
    variant_pk = variant.pk

    locked = threading.Event()
    outcome: list[object] = []
    errors: list[BaseException] = []

    def hold_the_lock() -> None:
        """Set mrp to 300 inside a transaction that holds the variant row lock."""
        try:
            with transaction.atomic():
                row = ProductVariant.objects.select_for_update().get(pk=variant_pk)
                locked.set()  # tell the other thread the lock is held
                time.sleep(0.5)  # keep it for a moment, so the other thread must wait
                row.mrp = Decimal("300.00")
                row.save(update_fields=["mrp", "updated_at"])
            # Leaving the ``with`` block commits, which releases the lock.
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()  # this thread's own connection, or the test DB cannot drop

    def import_while_locked() -> None:
        """Run the import: it must block on the lock, then see mrp 300."""
        try:
            assert locked.wait(timeout=10)
            outcome.append(apply_import(make_csv({}), actor=actor, filename="file.csv"))
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    first = threading.Thread(target=hold_the_lock)
    second = threading.Thread(target=import_while_locked)
    first.start()
    second.start()
    first.join(timeout=30)
    second.join(timeout=30)

    assert not errors, errors
    assert len(outcome) == 1
    assert isinstance(outcome[0], ApplyResult)
    assert outcome[0].updated == 1

    # If apply_import did not lock, it would have read mrp 280 (A had not committed
    # yet), found nothing to change, written no audit row and this lookup would fail.
    row = AuditLog.objects.get()
    assert row.action == VARIANT_UPDATE_ACTION
    assert row.changes["mrp"] == ["300.00", "280.00"]
    assert ProductVariant.objects.get(pk=variant_pk).mrp == Decimal("280.00")
