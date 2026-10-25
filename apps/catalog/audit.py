"""Variant audit helpers, shared by the admin now and by the CSV import (Slice 20)
and the bulk price update (Slice 21) later. This is where the audited field list
lives (ADR D44).
"""

from collections.abc import Mapping
from typing import Any

from django.contrib.auth.base_user import AbstractBaseUser

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
