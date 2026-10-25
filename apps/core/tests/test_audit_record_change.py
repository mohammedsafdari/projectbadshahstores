"""Tests for the audit write path (record_change and snapshot)."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from apps.catalog.tests.factories import ProductVariantFactory
from apps.core.audit import AuditError, record_change, snapshot

pytestmark = pytest.mark.django_db


def _make_user() -> Any:
    from apps.accounts.models import User

    return User.objects.create_user(phone="9876543210", password="ok")


def _variant() -> Any:
    return ProductVariantFactory()


# ---------------------------------------------------------------------------
# No change at all
# ---------------------------------------------------------------------------


def test_no_difference_returns_none_and_count_unchanged() -> None:
    variant = _variant()
    old = {"selling_price": Decimal("10.00")}
    new = {"selling_price": Decimal("10.00")}

    result = record_change(
        actor=_make_user(),
        action="PRICE_CHANGE",
        target=variant,
        old=old,
        new=new,
    )
    assert result is None
    assert variant.__class__.objects.filter(pk=variant.pk).exists()


def test_decimal_12_5_equals_12_50_no_difference() -> None:
    variant = _variant()
    result = record_change(
        actor=_make_user(),
        action="PRICE_CHANGE",
        target=variant,
        old={"selling_price": Decimal("12.5")},
        new={"selling_price": Decimal("12.50")},
    )
    assert result is None


def test_empty_new_returns_none() -> None:
    variant = _variant()
    result = record_change(
        actor=_make_user(),
        action="PRICE_CHANGE",
        target=variant,
        old={"selling_price": Decimal("10.00")},
        new={},
    )
    assert result is None


# ---------------------------------------------------------------------------
# A real change writes one row
# ---------------------------------------------------------------------------


def test_price_change_writes_one_row_with_expected_changes() -> None:
    variant = _variant()
    user = _make_user()

    entry = record_change(
        actor=user,
        action="PRICE_CHANGE",
        target=variant,
        old={"selling_price": Decimal("10.00")},
        new={"selling_price": Decimal("12.00")},
    )

    assert entry is not None
    assert entry.changes == {"selling_price": ["10.00", "12.00"]}


def test_only_differing_fields_stored_when_several_passed() -> None:
    variant = _variant()
    entry = record_change(
        actor=_make_user(),
        action="PRICE_CHANGE",
        target=variant,
        old={
            "selling_price": Decimal("10.00"),
            "mrp": Decimal("100.00"),
            "is_active": True,
        },
        new={
            "selling_price": Decimal("12.00"),
            "mrp": Decimal("100.00"),
            "is_active": True,
        },
    )

    assert entry is not None
    assert entry.changes == {"selling_price": ["10.00", "12.00"]}


def test_change_from_none_to_decimal_stored_as_none_and_string() -> None:
    variant = _variant()
    entry = record_change(
        actor=_make_user(),
        action="PRICE_CHANGE",
        target=variant,
        old={"cost_price": None},
        new={"cost_price": Decimal("5.00")},
    )

    assert entry is not None
    assert entry.changes == {"cost_price": [None, "5.00"]}


def test_bool_change_stored_as_true_and_false() -> None:
    variant = _variant()
    entry = record_change(
        actor=_make_user(),
        action="STATUS_CHANGE",
        target=variant,
        old={"is_active": True},
        new={"is_active": False},
    )

    assert entry is not None
    assert entry.changes == {"is_active": [True, False]}


def test_date_change_stored_as_iso_text() -> None:
    variant = _variant()
    today = date(2026, 10, 25)
    entry = record_change(
        actor=_make_user(),
        action="DATE_CHANGE",
        target=variant,
        old={"expiry_date": date(2026, 1, 1)},
        new={"expiry_date": today},
    )

    assert entry is not None
    assert entry.changes == {"expiry_date": ["2026-01-01", "2026-10-25"]}


def test_row_stores_actor_action_note_target_type_and_target_id() -> None:
    variant = _variant()
    user = _make_user()
    entry = record_change(
        actor=user,
        action="PRICE_CHANGE",
        target=variant,
        old={"selling_price": Decimal("10.00")},
        new={"selling_price": Decimal("12.00")},
        note="manual update",
    )

    assert entry is not None
    assert entry.actor_id == user.pk
    assert entry.action == "PRICE_CHANGE"
    assert entry.note == "manual update"
    assert entry.target_type == "catalog.productvariant"
    assert entry.target_id == str(variant.pk)


def test_actor_none_is_stored_as_none() -> None:
    variant = _variant()
    entry = record_change(
        actor=None,
        action="SYSTEM_CHANGE",
        target=variant,
        old={"selling_price": Decimal("10.00")},
        new={"selling_price": Decimal("12.00")},
    )

    assert entry is not None
    assert entry.actor is None


def test_action_is_stripped_of_surrounding_spaces() -> None:
    variant = _variant()
    entry = record_change(
        actor=_make_user(),
        action="  PRICE_CHANGE  ",
        target=variant,
        old={"selling_price": Decimal("10.00")},
        new={"selling_price": Decimal("12.00")},
    )

    assert entry is not None
    assert entry.action == "PRICE_CHANGE"


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


def test_blank_action_raises_audit_error() -> None:
    variant = _variant()
    with pytest.raises(AuditError):
        record_change(
            actor=_make_user(),
            action="   ",
            target=variant,
            old={"selling_price": Decimal("10.00")},
            new={"selling_price": Decimal("12.00")},
        )


def test_action_of_41_characters_raises_audit_error() -> None:
    variant = _variant()
    with pytest.raises(AuditError):
        record_change(
            actor=_make_user(),
            action="A" * 41,
            target=variant,
            old={"selling_price": Decimal("10.00")},
            new={"selling_price": Decimal("12.00")},
        )


def test_note_of_201_characters_raises_audit_error() -> None:
    variant = _variant()
    with pytest.raises(AuditError):
        record_change(
            actor=_make_user(),
            action="PRICE_CHANGE",
            target=variant,
            old={"selling_price": Decimal("10.00")},
            new={"selling_price": Decimal("12.00")},
            note="x" * 201,
        )


def test_key_in_new_missing_from_old_raises_audit_error() -> None:
    variant = _variant()
    with pytest.raises(AuditError):
        record_change(
            actor=_make_user(),
            action="PRICE_CHANGE",
            target=variant,
            old={"selling_price": Decimal("10.00")},
            new={"selling_price": Decimal("12.00"), "cost_price": Decimal("5.00")},
        )


def test_unsaved_target_raises_audit_error() -> None:
    # ProductVariantFactory.build() with its saved parents avoids creating an
    # orphan; the variant itself is not saved, so pk is None.
    product = ProductVariantFactory().product
    variant = ProductVariantFactory.build(product=product)

    with pytest.raises(AuditError):
        record_change(
            actor=_make_user(),
            action="PRICE_CHANGE",
            target=variant,
            old={"selling_price": Decimal("10.00")},
            new={"selling_price": Decimal("12.00")},
        )


def test_float_value_raises_audit_error() -> None:
    variant = _variant()
    with pytest.raises(AuditError, match="unsupported type"):
        record_change(
            actor=_make_user(),
            action="PRICE_CHANGE",
            target=variant,
            old={"selling_price": Decimal("10.00")},
            new={"selling_price": 12.0},
        )


def test_unsupported_type_raises_audit_error() -> None:
    variant = _variant()
    with pytest.raises(AuditError, match="unsupported type"):
        record_change(
            actor=_make_user(),
            action="PRICE_CHANGE",
            target=variant,
            old={"tags": []},
            new={"tags": ["a", "b"]},
        )


# ---------------------------------------------------------------------------
# Side-effect: a failed call writes nothing
# ---------------------------------------------------------------------------


def test_failed_call_writes_no_row() -> None:
    variant = _variant()
    before_count = variant.__class__.objects.count()

    with pytest.raises(AuditError):
        record_change(
            actor=_make_user(),
            action="   ",
            target=variant,
            old={"selling_price": Decimal("10.00")},
            new={"selling_price": Decimal("12.00")},
        )

    after_count = variant.__class__.objects.count()
    assert after_count == before_count


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


def test_snapshot_returns_field_values_in_order_for_a_variant() -> None:
    variant = _variant()
    result = snapshot(variant, ["mrp", "selling_price", "is_active"])

    assert result == {
        "mrp": variant.mrp,
        "selling_price": variant.selling_price,
        "is_active": variant.is_active,
    }
