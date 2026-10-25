"""Variant audit helpers, shared by the admin now and by the CSV import (Slice 20)
and the bulk price update (Slice 21) later. This is where the audited field list
lives (ADR D44).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.contrib.auth.base_user import AbstractBaseUser
from django.forms import BaseFormSet
from django.http import HttpRequest

from apps.catalog.models import ProductVariant
from apps.core.audit import record_change, snapshot
from apps.core.models import AuditLog

AUDITED_VARIANT_FIELDS: tuple[str, ...] = (
    "mrp",
    "selling_price",
    "cost_price",
    "gst_rate_percent",
    "is_active",
)

VARIANT_UPDATE_ACTION = "VARIANT_UPDATE"


def snapshot_variant_from_db(variant_pk: int) -> dict[str, Any]:
    """Read the stored values for the audited fields of a saved variant.

    This reads from the database, not from any unsaved changes on an
    in-memory object.
    """
    row = ProductVariant.objects.get(pk=variant_pk)
    return snapshot(row, AUDITED_VARIANT_FIELDS)


def record_variant_change(
    *,
    actor: AbstractBaseUser | None,
    variant: ProductVariant,
    old: Mapping[str, Any],
    note: str = "",
) -> AuditLog | None:
    """Write one AuditLog row when an audited variant field changed, else None."""
    new = snapshot(variant, AUDITED_VARIANT_FIELDS)
    return record_change(
        actor=actor,
        action=VARIANT_UPDATE_ACTION,
        target=variant,
        old=old,
        new=new,
        note=note,
    )


def audit_actor(request: HttpRequest) -> AbstractBaseUser | None:
    """The logged-in user behind a request, or None for anything else.

    The admin only serves logged-in staff. The isinstance check narrows the type
    from "user or anonymous user" to a real user for the type checker.
    """
    return request.user if isinstance(request.user, AbstractBaseUser) else None


def snapshot_variants_before_formset_save(formset: BaseFormSet[Any]) -> dict[int, dict[str, Any]]:
    """Stored audited values of every existing variant row that is about to change.

    Call this BEFORE the formset is saved: by then the forms have already put the
    new values on their instances, so only the database still holds the old ones.
    Rows ticked for deletion and rows the user did not touch are skipped, and so
    are brand-new rows (a new variant has no old values; D44 audits changes only).
    """
    deleted = list(formset.deleted_forms)
    old_by_pk: dict[int, dict[str, Any]] = {}
    for form in formset.initial_forms:
        pk = form.instance.pk
        if pk is None or form in deleted or not form.has_changed():
            continue
        old_by_pk[pk] = snapshot_variant_from_db(pk)
    return old_by_pk


def record_variant_formset_changes(
    *,
    actor: AbstractBaseUser | None,
    formset: BaseFormSet[Any],
    old_by_pk: Mapping[int, Mapping[str, Any]],
) -> list[AuditLog]:
    """Write one AuditLog row per variant whose audited fields changed.

    Call this AFTER the formset is saved, with the result of
    snapshot_variants_before_formset_save.
    """
    rows: list[AuditLog] = []
    for form in formset.initial_forms:
        old = old_by_pk.get(form.instance.pk)
        if old is None:
            continue
        row = record_variant_change(actor=actor, variant=form.instance, old=old)
        if row is not None:
            rows.append(row)
    return rows
