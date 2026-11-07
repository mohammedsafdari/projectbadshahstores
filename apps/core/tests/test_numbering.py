"""Tests for the financial-year label and the order number series (D14, D64)."""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from apps.core.models import NumberSeriesCounter
from apps.core.numbering import financial_year_label, next_number

pytestmark = pytest.mark.django_db

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=IST)


def test_the_financial_year_starts_on_the_first_of_april() -> None:
    assert financial_year_label(date(2026, 3, 31)) == "25-26"
    assert financial_year_label(date(2026, 4, 1)) == "26-27"


def test_the_financial_year_wraps_the_century() -> None:
    assert financial_year_label(date(2099, 4, 1)) == "99-00"
    assert financial_year_label(date(2100, 4, 1)) == "00-01"


def test_an_aware_datetime_is_read_in_the_shop_time_zone() -> None:
    # 20:00 UTC on 31 March is already 01:30 on 1 April in Indore.
    when = datetime(2026, 3, 31, 20, 0, tzinfo=UTC)

    assert financial_year_label(when) == "26-27"


def test_a_naive_datetime_is_refused() -> None:
    with pytest.raises(ValueError):
        financial_year_label(datetime(2026, 11, 7, 12, 0))


def test_next_number_starts_at_one_and_counts_up() -> None:
    assert next_number("BS", when=NOW) == "BS/26-27/000001"
    assert next_number("BS", when=NOW) == "BS/26-27/000002"


def test_next_number_keeps_a_separate_counter_per_financial_year() -> None:
    first = next_number("BS", when=NOW)
    second = next_number("BS", when=datetime(2027, 4, 1, 12, 0, tzinfo=IST))

    assert first == "BS/26-27/000001"
    assert second == "BS/27-28/000001"


def test_next_number_keeps_a_separate_counter_per_prefix() -> None:
    assert next_number("BS", when=NOW) == "BS/26-27/000001"
    assert next_number("INV", when=NOW) == "INV/26-27/000001"
    assert next_number("BS", when=NOW) == "BS/26-27/000002"


def test_next_number_strips_the_prefix() -> None:
    assert next_number("  BS  ", when=NOW) == "BS/26-27/000001"


def test_next_number_refuses_a_blank_prefix() -> None:
    with pytest.raises(ValueError):
        next_number("", when=NOW)
    with pytest.raises(ValueError):
        next_number("   ", when=NOW)


def test_next_number_rolls_back_the_counter_with_the_transaction() -> None:
    with pytest.raises(RuntimeError, match="boom"), transaction.atomic():
        next_number("BS", when=NOW)
        raise RuntimeError("boom")

    assert next_number("BS", when=NOW) == "BS/26-27/000001"


def test_next_number_moves_the_counter_updated_at() -> None:
    next_number("BS", when=NOW)
    counter = NumberSeriesCounter.objects.get(prefix="BS", financial_year="26-27")
    before = counter.updated_at

    next_number("BS", when=NOW)

    counter.refresh_from_db()
    assert counter.updated_at > before


def test_next_number_locks_the_counter_row() -> None:
    # The first call creates the counter row, so the measured call updates it.
    next_number("BS", when=NOW)

    with CaptureQueriesContext(connection) as captured:
        next_number("BS", when=NOW)

    table = NumberSeriesCounter._meta.db_table
    # The generator inside any() stops at the first matching query.
    assert any(
        "FOR UPDATE" in query["sql"] and table in query["sql"]
        for query in captured.captured_queries
    )


def test_next_number_does_not_truncate_after_six_digits() -> None:
    NumberSeriesCounter.objects.create(
        prefix="BS",
        financial_year="26-27",
        last_number=999999,
    )

    assert next_number("BS", when=NOW) == "BS/26-27/1000000"


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_calls_never_take_the_same_number() -> None:
    barrier = threading.Barrier(2)
    numbers: list[str] = []
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            barrier.wait(timeout=10)
            numbers.append(next_number("BS", when=NOW))
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(numbers) == 2
    assert len(set(numbers)) == 2
    counter = NumberSeriesCounter.objects.get(prefix="BS", financial_year="26-27")
    assert counter.last_number == 2
