"""Order and invoice number series (D14, D64).

The counter lives in ``core.NumberSeriesCounter``, keyed by ``(prefix,
financial_year)``. ``next_number`` is always called inside a transaction,
locks the one counter row with ``select_for_update()`` and increments it, so a
rolled-back order leaves no gap and two callers never get the same number.

Money and time rules do not live here: this module only formats a number.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.core.models import NumberSeriesCounter

# Financial years are Indian accounting years and the label is always read in
# the shop time zone, never in UTC (D4, D14).
SHOP_TIME_ZONE = ZoneInfo("Asia/Kolkata")


def _local_year_and_month(when: datetime | date) -> tuple[int, int]:
    """Return the shop-local year and month for a date or an aware datetime."""
    if isinstance(when, datetime):
        # A naive datetime has no time zone, so it cannot be converted safely.
        if timezone.is_naive(when):
            raise ValueError("numbering needs an aware datetime.")
        local = when.astimezone(SHOP_TIME_ZONE)
        return local.year, local.month
    return when.year, when.month


def financial_year_label(when: datetime | date) -> str:
    """Return the Indian financial-year label for a date or aware datetime.

    The year starts on 1 April: 31 March 2026 is "25-26" and 1 April 2026 is
    "26-27". An aware datetime is converted to ``Asia/Kolkata`` first, so
    31 March 20:00 UTC is already 1 April in Indore.
    """
    year, month = _local_year_and_month(when)
    start = year if month >= 4 else year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def next_number(prefix: str, when: datetime | date | None = None) -> str:
    """Return the next number as ``prefix/financial_year/000001``.

    ``prefix`` must not be blank. ``when`` defaults to ``timezone.now()`` and
    must be an aware datetime when it is a datetime. The function opens its own
    ``atomic()`` block as well, so it is safe when the caller already holds one.
    """
    # "Blank" means empty after stripping spaces, the same rule the shop uses
    # for a not-configured prefix (D58). A blank prefix would make a meaningless
    # number, so it is refused rather than silently replaced here.
    cleaned = prefix.strip()
    if not cleaned:
        raise ValueError("A number series needs a prefix.")

    if when is None:
        when = timezone.now()
    label = financial_year_label(when)

    with transaction.atomic():
        # get_or_create handles the first-use race: if two transactions both
        # try to create the row, one insert wins and the other re-reads it.
        counter, _created = NumberSeriesCounter.objects.get_or_create(
            prefix=cleaned,
            financial_year=label,
        )
        # The row lock serialises the increment. It must be a second read,
        # because the object returned by get_or_create is not locked.
        counter = NumberSeriesCounter.objects.select_for_update().get(pk=counter.pk)
        counter.last_number += 1
        # auto_now writes updated_at only when it is named in update_fields.
        counter.save(update_fields=["last_number", "updated_at"])
        return f"{cleaned}/{label}/{counter.last_number:06d}"
