"""Mark selected variants as sold loose, or back as packets (Slice 29c).

The admin list calls ``set_variants_loose`` on the selected rows. One transaction
locks them in ``pk`` order, decides each row under the lock and writes only the
rows that change, each with its audit row (D44 and the new D56).

Rules (D42, D55 amendment and D56):

- a loose variant has ``is_sold_loose=True``, unit ``KG`` or ``L`` and pack size 1.
  The database already enforces that (``catalog_variant_loose_unit_and_pack_size``);
  a row that breaks it is skipped with a reason and never written;
- a variant newly marked loose gets ``quantity_step = 0.001``, the smallest amount
  a customer may order (D55 amendment). A variant that is already loose is left
  alone, including its own step, and writes no audit row;
- marking as a packet sets ``is_sold_loose=False`` and ``quantity_step=1``, which
  the database requires for packets. An already-packet variant is left alone;
- writes use ``save(update_fields=[...])`` (never ``queryset.update()``), so
  ``updated_at`` moves and the audit row is written beside the change, exactly as
  the bulk price update does (D44, D47).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from django.contrib.auth.base_user import AbstractBaseUser
from django.db import transaction

from apps.catalog.audit import record_variant_change, snapshot_variant_from_db
from apps.catalog.models import ProductVariant
from apps.catalog.price_update import MAX_VARIANTS

# One gram (or one millilitre): the smallest step the owner agreed to (D55 amendment).
LOOSE_QUANTITY_STEP = Decimal("0.001")
PACKET_QUANTITY_STEP = Decimal("1")

# A loose variant's unit; the price is per kg or per litre.
LOOSE_UNITS: tuple[str, ...] = (ProductVariant.Unit.KG, ProductVariant.Unit.L)

# Pack size 1 makes the selling price a price per unit of the variant's unit.
UNIT_PACK_SIZE = Decimal("1")

LOOSE_NOTE = "Marked sold loose (admin action)"
PACKET_NOTE = "Marked as packet (admin action)"


@dataclass(frozen=True)
class LooseSkip:
    """One selected variant left alone, with the reason a person can read."""

    sku_code: str
    reason: str


@dataclass(frozen=True)
class LooseMarkResult:
    """What a run did: how many changed, how many already were, and the skips."""

    changed: int
    unchanged: int
    skipped: list[LooseSkip]


class LooseMarkingRejected(Exception):
    """Raised when a run is refused before any row is written."""


def set_variants_loose(
    pks: Iterable[int], *, loose: bool, actor: AbstractBaseUser | None
) -> LooseMarkResult:
    """Mark every selected variant as sold loose (``loose=True``) or as a packet.

    Returns the counts and the skipped rows. ``actor`` goes into every audit row
    (None for a system run). Nothing is written when the run is refused.
    """
    # set() drops a repeated id (a row must not be written twice) and sorted keeps
    # the lock order stable, so two overlapping runs queue instead of deadlocking (D43).
    selected = sorted(set(pks))
    if len(selected) > MAX_VARIANTS:
        raise LooseMarkingRejected(f"Select at most {MAX_VARIANTS} variants per run.")

    # One transaction: either every changed row and its audit row are saved, or none.
    with transaction.atomic():
        return _apply_locked(selected, loose=loose, actor=actor)


def _apply_locked(
    pks: list[int], *, loose: bool, actor: AbstractBaseUser | None
) -> LooseMarkResult:
    """Do the work with the row locks held. Call it inside ``transaction.atomic()``.

    ``list()`` matters because a queryset is lazy: ``select_for_update()`` locks
    the rows when they are actually read, not when the queryset is built.
    """
    locked = list(ProductVariant.objects.select_for_update().filter(pk__in=pks).order_by("pk"))

    note = LOOSE_NOTE if loose else PACKET_NOTE
    changed = 0
    unchanged = 0
    skipped: list[LooseSkip] = []
    for variant in locked:
        reason = _skip_reason(variant, loose=loose)
        if reason is not None:
            skipped.append(LooseSkip(variant.sku_code, reason))
            continue

        if variant.is_sold_loose == loose:
            # Already in the wanted state: keep its own quantity step and write nothing.
            unchanged += 1
            continue

        # Take the old values BEFORE changing the instance: the audit row needs both
        # sides of the change.
        old = snapshot_variant_from_db(variant.pk)
        variant.is_sold_loose = loose
        variant.quantity_step = LOOSE_QUANTITY_STEP if loose else PACKET_QUANTITY_STEP
        # full_clean() runs the field validators and the database check constraints
        # (unit and pack size for loose rows, step 1 for packets) before any write.
        variant.full_clean()
        # update_fields writes only the named columns, so updated_at must be listed:
        # with update_fields Django does not add auto_now fields itself.
        variant.save(update_fields=["is_sold_loose", "quantity_step", "updated_at"])
        record_variant_change(actor=actor, variant=variant, old=old, note=note)
        changed += 1

    return LooseMarkResult(changed=changed, unchanged=unchanged, skipped=skipped)


def _skip_reason(variant: ProductVariant, *, loose: bool) -> str | None:
    """Why this variant cannot be marked, or None when the run may change it."""
    if not loose:
        # Any variant can become a packet: the only rule is step 1, which we set.
        return None
    if variant.unit not in LOOSE_UNITS:
        return f"Unit {variant.unit} is not kg or litre."
    if variant.pack_size != UNIT_PACK_SIZE:
        return f"Pack size {variant.pack_size} is not 1, so the price would not be per kg."
    return None
