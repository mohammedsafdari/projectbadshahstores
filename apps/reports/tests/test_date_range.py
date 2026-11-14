"""The ``from`` and ``to`` range: defaults, limits and bad input (D85)."""

from __future__ import annotations

from datetime import date

import pytest
from django.urls import reverse

from apps.reports.queries import (
    BAD_RANGE_DATE,
    LONG_RANGE,
    MAX_RANGE_DAYS,
    REVERSED_RANGE,
    DateRange,
    parse_range,
)
from apps.reports.tests.helpers import staff_client

pytestmark = pytest.mark.django_db

TODAY = date(2026, 11, 14)


# ---------------------------------------------------------------------------
# The pure range reader
# ---------------------------------------------------------------------------
def test_a_blank_range_is_this_month_so_far() -> None:
    period = parse_range(None, None, today=TODAY)

    assert period.start == date(2026, 11, 1)
    assert period.end == TODAY
    assert period.ok is True


def test_both_dates_are_read_and_both_are_included() -> None:
    period = parse_range("2026-11-03", "2026-11-09", today=TODAY)

    assert period == DateRange(date(2026, 11, 3), date(2026, 11, 9))


def test_a_single_day_is_allowed() -> None:
    period = parse_range("2026-11-09", "2026-11-09", today=TODAY)

    assert period.start == period.end == date(2026, 11, 9)
    assert period.ok is True


@pytest.mark.parametrize("value", ["14-11-2026", "2026-13-01", "yesterday", "2026/11/01"])
def test_a_date_that_is_not_a_date_is_refused_with_a_sentence(value: str) -> None:
    period = parse_range(value, None, today=TODAY)

    assert period.ok is False
    assert period.error == BAD_RANGE_DATE


def test_a_reversed_range_is_refused_with_a_sentence() -> None:
    period = parse_range("2026-11-10", "2026-11-01", today=TODAY)

    assert period.ok is False
    assert period.error == REVERSED_RANGE


def test_a_range_of_exactly_the_limit_is_allowed() -> None:
    period = parse_range("2026-01-01", "2027-01-01", today=TODAY)

    assert (period.end - period.start).days + 1 == MAX_RANGE_DAYS
    assert period.ok is True


def test_a_range_one_day_longer_than_the_limit_is_refused() -> None:
    period = parse_range("2026-01-01", "2027-01-02", today=TODAY)

    assert period.ok is False
    assert period.error == LONG_RANGE


# ---------------------------------------------------------------------------
# The pages
# ---------------------------------------------------------------------------
def test_a_bad_date_on_the_page_is_a_message_not_a_crash() -> None:
    response = staff_client().get(reverse("reports_sales"), {"from": "nope"})

    assert response.status_code == 200
    assert BAD_RANGE_DATE in response.content.decode()


def test_a_reversed_range_on_the_page_shows_its_own_sentence() -> None:
    response = staff_client().get(
        reverse("reports_gst"),
        {"from": "2026-11-10", "to": "2026-11-01"},
    )

    assert response.status_code == 200
    assert REVERSED_RANGE in response.content.decode()


def test_a_bad_range_on_a_csv_goes_back_to_its_page_with_a_message() -> None:
    response = staff_client().get(
        reverse("reports_sales_csv"),
        {"from": "2026-11-10", "to": "2026-11-01"},
        follow=True,
    )

    assert response.status_code == 200
    assert REVERSED_RANGE in response.content.decode()
