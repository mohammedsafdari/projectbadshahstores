"""The backup key format and the retention rule (D96).

These are the pure functions of `apps/core/backups.py`: no bucket, no network
and no clock, because a key already says when its backup was taken. Every test
therefore gets the same answer whenever it runs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from apps.core import backups


def _key(year: int, month: int, day: int, hour: int = 2, minute: int = 30) -> str:
    """The key a backup taken at that UTC time would have."""
    return backups.backup_key(datetime(year, month, day, hour, minute, tzinfo=UTC))


def test_backup_key_is_utc_and_sortable() -> None:
    # 08:00 in India (+05:30) is 02:30 UTC, which is the time the key must show.
    india = timezone(timedelta(hours=5, minutes=30))
    moment = datetime(2026, 11, 16, 8, 0, tzinfo=india)

    assert backups.backup_key(moment) == "backups/2026-11-16T0230Z.dump.age"

    # Plain text sorting and time ordering agree: the biggest part comes first.
    earlier = _key(2026, 11, 16, 2, 30)
    later = _key(2026, 11, 16, 3, 30)
    assert sorted([later, earlier]) == [earlier, later]


def test_backup_key_refuses_a_naive_time() -> None:
    # No zone says nothing about which moment this is, so it is refused.
    with pytest.raises(ValueError):
        backups.backup_key(datetime(2026, 11, 16, 2, 30))


def test_parse_round_trips_a_key() -> None:
    india = timezone(timedelta(hours=5, minutes=30))
    moment = datetime(2026, 11, 16, 8, 0, tzinfo=india)

    parsed = backups.parse_backup_time(backups.backup_key(moment))

    assert parsed == moment
    assert parsed is not None
    # The result is aware and in UTC, which is what every rule groups by.
    assert parsed.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "key",
    [
        "media/2026-11-16T0230Z.dump.age",
        "backups/2026-11-16T0230Z.dump",
        "backups/2026-11-16T0230Z.dump.age-extra",
        "backups/2026-13-45T0230Z.dump.age",
    ],
    ids=["other prefix", "other suffix", "extra character", "impossible date"],
)
def test_parse_rejects_foreign_names(key: str) -> None:
    assert backups.parse_backup_time(key) is None


def test_nothing_to_delete_for_an_empty_list() -> None:
    assert backups.select_keys_to_delete([]) == []


def test_the_newest_key_is_always_kept() -> None:
    # One backup a month for a year, so all three windows have work to do.
    keys = [_key(2025, month, 15) for month in range(1, 13)]
    newest = keys[-1]

    doomed = backups.select_keys_to_delete(keys)

    assert doomed
    assert newest not in doomed


def test_ten_daily_backups_keep_the_newest_seven_plus_older_weekly_and_monthly_ones() -> None:
    # Ten consecutive days: 7 to 16 November 2026. The three ISO weeks are
    # W45 (2 to 8 November), W46 (9 to 15) and W47 (16 to 22).
    keys = [_key(2026, 11, day) for day in range(7, 17)]

    doomed = backups.select_keys_to_delete(keys)

    # The daily window keeps 10 to 16 November, and the weekly window keeps
    # 8 November (the newest of W45). 7 November is the 8th newest day and not
    # a week's newest, and 9 November is neither 7 days old nor a week's newest.
    assert doomed == [_key(2026, 11, 7), _key(2026, 11, 9)]


def test_the_newest_backup_of_each_week_is_kept() -> None:
    # Two backups in each of two ISO weeks: 5 and 6 January (W02), 12 and 13 (W03).
    first_week = [_key(2026, 1, 5), _key(2026, 1, 6)]
    second_week = [_key(2026, 1, 12), _key(2026, 1, 13)]
    keys = [*first_week, *second_week]

    # Two weeks kept means the newest of each: 6 January and 13 January.
    assert backups.select_keys_to_delete(keys, keep_daily=0, keep_weekly=2, keep_monthly=0) == [
        first_week[0],
        second_week[0],
    ]
    # One week kept drops the whole older week, its newest backup included.
    assert backups.select_keys_to_delete(keys, keep_daily=0, keep_weekly=1, keep_monthly=0) == [
        *first_week,
        second_week[0],
    ]


def test_the_newest_backup_of_each_month_is_kept() -> None:
    january = [_key(2026, 1, 5), _key(2026, 1, 6)]
    february = [_key(2026, 2, 5), _key(2026, 2, 6)]
    march = [_key(2026, 3, 5), _key(2026, 3, 6)]

    doomed = backups.select_keys_to_delete(
        [*january, *february, *march], keep_daily=0, keep_weekly=0, keep_monthly=2
    )

    # Only February's newest (6 February) and March's newest survive, so both
    # of January's go and so do the two older backups of the kept months.
    assert doomed == [january[0], january[1], february[0], march[0]]


def test_two_backups_on_one_day_keep_only_the_newest() -> None:
    morning = _key(2026, 11, 16, 2, 30)
    evening = _key(2026, 11, 16, 21, 15)

    assert backups.select_keys_to_delete([morning, evening], 1, 0, 0) == [morning]


def test_unknown_keys_are_never_returned() -> None:
    old = _key(2026, 1, 1)
    new = _key(2026, 1, 2)
    strangers = [
        "backups/README.txt",
        "media/2026-01-03T0230Z.dump.age",
        "backups/2026-01-04T0230Z.dump.age.part",
    ]

    doomed = backups.select_keys_to_delete([*strangers, new, old], 1, 0, 0)

    # The one old backup is deleted; a name we do not understand never is.
    assert doomed == [old]
    for stranger in strangers:
        assert stranger not in doomed


def test_result_is_sorted_oldest_first() -> None:
    newest = _key(2026, 3, 3)
    middle = _key(2026, 3, 2)
    oldest = _key(2026, 3, 1)

    assert backups.select_keys_to_delete([newest, oldest, middle], 1, 0, 0) == [oldest, middle]


def test_applying_the_rule_twice_deletes_nothing_more() -> None:
    # Forty daily backups across three months, so all three windows do work.
    keys = [
        backups.backup_key(datetime(2026, 1, 1, 2, 30, tzinfo=UTC) + timedelta(days=offset))
        for offset in range(40)
    ]

    doomed = backups.select_keys_to_delete(keys)
    survivors = [key for key in keys if key not in doomed]

    assert doomed
    # The rule is idempotent: running it on what it kept finds nothing to drop,
    # which is what makes a nightly run safe to repeat.
    assert backups.select_keys_to_delete(survivors) == []


def test_key_order_in_the_input_does_not_matter() -> None:
    keys = [_key(2026, 11, day) for day in range(7, 17)]
    shuffled = list(reversed(keys))

    assert backups.select_keys_to_delete(shuffled) == backups.select_keys_to_delete(keys)


def test_the_week_rule_uses_the_iso_year_across_new_year() -> None:
    # 29 December 2025 is a Monday, which is week 1 of ISO year 2026; the
    # Sunday before it belongs to week 52 of 2025.
    sunday = _key(2025, 12, 28)
    monday = _key(2025, 12, 29)
    next_monday = _key(2026, 1, 5)

    doomed = backups.select_keys_to_delete(
        [sunday, monday, next_monday], keep_daily=0, keep_weekly=2, keep_monthly=0
    )

    # W01 of 2026 and W02 of 2026 are the newest two weeks, so only the Sunday
    # of W52 goes. Grouping by the plain calendar year would split W01 in two.
    assert doomed == [sunday]
