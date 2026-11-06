"""Stock reservation services (D6, D7, D22, D63).

A reservation holds stock for one checkout without touching the ledger. Every
state change runs in one transaction, locks its rows and writes each row with
``save(update_fields=[...])``; queryset ``update()`` is never used, so
``updated_at`` always moves and no write skips the model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import ProductVariant
from apps.catalog.purchasable import purchasable_variant_ids
from apps.core.services import get_shop_settings
from apps.inventory.availability import get_available_quantities
from apps.inventory.models import StockReservation
from apps.inventory.services import InventoryError, _clean_quantity, _lock_variant


class ReservationError(InventoryError):
    """Base error for reservation service problems."""


@dataclass(frozen=True)
class Shortfall:
    """One line that cannot be reserved, with both amounts for the message."""

    variant_id: int
    requested: Decimal
    available: Decimal


class ReservationShortfallError(ReservationError):
    """One or more lines ask for more than availability allows."""

    def __init__(self, shortfalls: list[Shortfall]) -> None:
        self.shortfalls = shortfalls
        detail = ", ".join(
            f"variant {item.variant_id}: asked {item.requested}, available {item.available}"
            for item in shortfalls
        )
        super().__init__(f"Not enough stock to reserve ({detail}).")


class VariantNotReservableError(ReservationError):
    """A variant does not exist or is not visible to the storefront."""


class ReservationGroupExistsError(ReservationError):
    """The checkout group already has reservation rows."""


class ReservationStateError(ReservationError):
    """A reservation is not in a state that allows the requested change."""


class ReservationExpiredError(ReservationStateError):
    """A checkout hold has expired and can no longer be held for fulfilment."""


class ReservationNotFoundError(ReservationError):
    """No reservation or group matches the id given."""


@dataclass(frozen=True)
class ReservationLine:
    """One requested variant and amount, before anything is written."""

    variant_id: int
    quantity: Decimal


def reserve_stock(
    *,
    user: User,
    lines: Sequence[ReservationLine],
    kind: str,
    checkout_group: UUID | None = None,
    now: datetime | None = None,
) -> list[StockReservation]:
    """Reserve every line for one checkout group, or write nothing.

    The whole call is one transaction. Variant rows are locked in ascending
    ``pk`` order before availability is read, so two checkouts with the same
    items in a different order cannot deadlock (D43, D63).
    """
    if kind not in (
        StockReservation.Kind.CHECKOUT_PENDING_PAYMENT,
        StockReservation.Kind.HELD_FOR_FULFILMENT,
    ):
        raise ReservationError(f"Unknown reservation kind {kind!r}.")
    if not lines:
        raise ReservationError("A reservation needs at least one line.")

    variant_ids = [line.variant_id for line in lines]
    if len(set(variant_ids)) != len(variant_ids):
        raise ReservationError("A variant may appear only once per reservation.")

    resolved_now = now if now is not None else timezone.now()

    with transaction.atomic():
        variants = {
            variant.pk: variant for variant in ProductVariant.objects.filter(pk__in=variant_ids)
        }
        if set(variants) != set(variant_ids):
            raise VariantNotReservableError("One or more variants do not exist.")
        if purchasable_variant_ids(variant_ids) != set(variant_ids):
            raise VariantNotReservableError("One or more variants are not for sale.")

        # Locking in ascending pk order gives every transaction the same lock
        # order. Without it, two checkouts holding the same two items in
        # opposite orders could each take one lock and wait forever.
        locked: dict[int, ProductVariant] = {}
        for variant_id in sorted(variant_ids):
            locked[variant_id] = _lock_variant(variants[variant_id])

        cleaned: list[ReservationLine] = []
        for line in lines:
            quantity = _clean_quantity(line.quantity, variant=locked[line.variant_id])
            if quantity <= 0:
                raise ReservationError("A reservation quantity must be above zero.")
            cleaned.append(ReservationLine(variant_id=line.variant_id, quantity=quantity))

        # The group check runs after the variant locks, so two reserves with
        # the same group and the same variants queue instead of both passing.
        if (
            checkout_group is not None
            and StockReservation.objects.filter(checkout_group=checkout_group).exists()
        ):
            raise ReservationGroupExistsError(
                f"Checkout group {checkout_group} already has reservations."
            )

        available = get_available_quantities(variant_ids, now=resolved_now)
        shortfalls = [
            Shortfall(
                variant_id=line.variant_id,
                requested=line.quantity,
                available=available[line.variant_id],
            )
            for line in cleaned
            if line.quantity > available[line.variant_id]
        ]
        if shortfalls:
            raise ReservationShortfallError(shortfalls)

        if kind == StockReservation.Kind.CHECKOUT_PENDING_PAYMENT:
            expires_at: datetime | None = resolved_now + timedelta(
                minutes=get_shop_settings().reservation_minutes
            )
        else:
            expires_at = None

        group = checkout_group if checkout_group is not None else uuid4()
        rows = [
            StockReservation.objects.create(
                variant=locked[line.variant_id],
                user=user,
                quantity=line.quantity,
                status=StockReservation.Status.ACTIVE,
                kind=kind,
                checkout_group=group,
                expires_at=expires_at,
            )
            for line in cleaned
        ]
        return rows


def hold_for_fulfilment(checkout_group: UUID, now: datetime | None = None) -> int:
    """Turn a group's checkout holds into fulfilment holds (D22).

    Returns the number of rows changed. A group that is already entirely held
    returns 0, so calling it twice is safe. An expired checkout hold can no
    longer be held; that is ``ReservationExpiredError`` (D9).
    """
    resolved_now = now if now is not None else timezone.now()
    with transaction.atomic():
        rows = list(
            StockReservation.objects.select_for_update()
            .filter(checkout_group=checkout_group)
            .order_by("pk")
        )
        if not rows:
            raise ReservationNotFoundError(f"No reservations for group {checkout_group}.")

        already_held = all(
            row.status == StockReservation.Status.ACTIVE
            and row.kind == StockReservation.Kind.HELD_FOR_FULFILMENT
            for row in rows
        )
        if already_held:
            return 0

        for row in rows:
            if (
                row.status != StockReservation.Status.ACTIVE
                or row.kind != StockReservation.Kind.CHECKOUT_PENDING_PAYMENT
            ):
                raise ReservationStateError(
                    "Only active checkout reservations can be held for fulfilment."
                )
            if row.expires_at is not None and row.expires_at <= resolved_now:
                raise ReservationExpiredError("The checkout reservation has expired.")

        for row in rows:
            row.kind = StockReservation.Kind.HELD_FOR_FULFILMENT
            row.expires_at = None
            row.save(update_fields=["kind", "expires_at", "updated_at"])
        return len(rows)


def release_group(checkout_group: UUID, now: datetime | None = None) -> int:
    """Release every active row of a group and return how many changed.

    Consumed and released rows are left alone, so a second call returns 0.
    An unknown group returns 0 as well.
    """
    resolved_now = now if now is not None else timezone.now()
    with transaction.atomic():
        rows = list(
            StockReservation.objects.select_for_update()
            .filter(checkout_group=checkout_group)
            .order_by("pk")
        )
        released = 0
        for row in rows:
            if row.status != StockReservation.Status.ACTIVE:
                continue
            row.status = StockReservation.Status.RELEASED
            row.released_at = resolved_now
            row.save(update_fields=["status", "released_at", "updated_at"])
            released += 1
        return released


def consume_reservation(reservation_id: int, now: datetime | None = None) -> StockReservation:
    """Mark one held row consumed. It writes no stock movement (D7).

    The single ``SALE`` writer arrives with packing in Phase 4 and will call
    this inside the same transaction.
    """
    resolved_now = now if now is not None else timezone.now()
    with transaction.atomic():
        row = StockReservation.objects.select_for_update().filter(pk=reservation_id).first()
        if row is None:
            raise ReservationNotFoundError(f"No reservation with id {reservation_id}.")
        if (
            row.status != StockReservation.Status.ACTIVE
            or row.kind != StockReservation.Kind.HELD_FOR_FULFILMENT
        ):
            raise ReservationStateError("Only an active fulfilment hold can be consumed.")

        row.status = StockReservation.Status.CONSUMED
        row.consumed_at = resolved_now
        row.save(update_fields=["status", "consumed_at", "updated_at"])
        return row


def expire_stale_reservations(now: datetime | None = None) -> int:
    """Release checkout holds whose expiry has passed; return how many.

    Nothing schedules this until online payments exist (D63). It is
    idempotent: a second call finds no active expired rows and returns 0.
    """
    resolved_now = now if now is not None else timezone.now()
    with transaction.atomic():
        rows = list(
            StockReservation.objects.select_for_update()
            .filter(
                status=StockReservation.Status.ACTIVE,
                kind=StockReservation.Kind.CHECKOUT_PENDING_PAYMENT,
                expires_at__lte=resolved_now,
            )
            .order_by("pk")
        )
        for row in rows:
            row.status = StockReservation.Status.RELEASED
            row.released_at = resolved_now
            row.save(update_fields=["status", "released_at", "updated_at"])
        return len(rows)
