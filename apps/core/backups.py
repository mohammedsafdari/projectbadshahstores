"""Encrypted database backups in a private Cloudflare R2 bucket (D96).

The host script (Slice 54b) makes the dump with ``pg_dump``, checks it, encrypts
it with ``age``, and pipes the encrypted file into the ``upload_backup``
command. Nothing in this module ever sees the plain dump or the private ``age``
key: it only names a file, decides which old files the retention rule may
delete, and builds the S3 client that talks to R2.

Keeping the naming and the rule here, apart from the commands, is what makes
them testable without a network, a bucket or a clock: a key already says when
the backup was taken, so no rule needs to know today's date.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.config import Config
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

# Every backup lives under this folder of the bucket, so a listing asks R2 for
# only that part of the bucket.
BACKUP_PREFIX = "backups/"

# The suffix says what the file is: ``.dump`` is PostgreSQL's custom format,
# ``.age`` means it is encrypted with the owner's age public key.
BACKUP_SUFFIX = ".dump.age"

# ``strftime`` reads the percent signs as placeholders: %Y the four-digit year,
# %m the month, %d the day, %H the hour (24-hour clock), %M the minute, and the
# ``T`` and ``Z`` are literal characters that say "UTC". One example is
# ``2026-11-16T0230Z``, which sorts correctly as plain text because the biggest
# part of the date comes first.
BACKUP_TIME_FORMAT = "%Y-%m-%dT%H%MZ"

# The retention rule, in three independent windows (D96): every one of the last
# seven days, every one of the last four calendar weeks, and every one of the
# last three months. A backup is kept when any of the three windows names it.
KEEP_DAILY = 7
KEEP_WEEKLY = 4
KEEP_MONTHLY = 3

# A real encrypted dump is megabytes. Anything this small means ``pg_dump``
# failed, and a failed dump must never be uploaded as a "successful" backup.
MIN_BACKUP_BYTES = 1024


def backup_key(moment: datetime) -> str:
    """Return the bucket key for a backup taken at ``moment``.

    The moment is converted to UTC first, so the same instant always gets the
    same key whatever zone the server itself runs in.
    """
    # A "naive" datetime is one that does not say which zone it is in, so two
    # servers could name the same backup differently. ``utcoffset()`` is None
    # for exactly those, and this refuses them rather than guessing.
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("backup_key needs a time-zone-aware datetime.")

    utc = moment.astimezone(UTC)
    # An f-string puts the variables inside the text: the braces are replaced
    # by their values.
    return f"{BACKUP_PREFIX}{utc.strftime(BACKUP_TIME_FORMAT)}{BACKUP_SUFFIX}"


def parse_backup_time(key: str) -> datetime | None:
    """Return when the backup named by ``key`` was taken, or None.

    This is the exact inverse of :func:`backup_key`: a key with another folder,
    another suffix, a stray character or an impossible date is not a backup,
    and None says so.
    """
    if not key.startswith(BACKUP_PREFIX):
        return None

    # Slicing a string: ``key[len(BACKUP_PREFIX):]`` is everything after the
    # folder, and ``[:-len(BACKUP_SUFFIX)]`` drops the suffix from the end.
    stamp = key[len(BACKUP_PREFIX) :]
    if not stamp.endswith(BACKUP_SUFFIX):
        return None
    stamp = stamp[: -len(BACKUP_SUFFIX)]

    try:
        parsed = datetime.strptime(stamp, BACKUP_TIME_FORMAT)
    except ValueError:
        # An impossible date such as month 13, or text that is not a date at
        # all, raises ValueError; it simply is not one of our keys.
        return None

    # ``strptime`` is forgiving about leading zeros ("2026-1-6T230Z" parses),
    # so the text is written back out and compared: only the one exact shape
    # is accepted, which is what makes this the exact inverse.
    if parsed.strftime(BACKUP_TIME_FORMAT) != stamp:
        return None

    # ``replace(tzinfo=...)`` attaches the UTC zone to a datetime that was
    # built without one, so the result is "aware" like backup_key's input.
    return parsed.replace(tzinfo=UTC)


def _utc_day(moment: datetime) -> tuple[int, int, int]:
    """Return the UTC calendar day of ``moment``, as three numbers that sort."""
    return moment.year, moment.month, moment.day


def _utc_month(moment: datetime) -> tuple[int, int]:
    """Return the UTC calendar month of ``moment``, as two numbers that sort."""
    return moment.year, moment.month


def _iso_week(moment: datetime) -> tuple[int, int]:
    """Return the ISO year and week number of ``moment``.

    ``isocalendar()`` follows ISO 8601, where a week belongs to the year that
    holds its Thursday: 29 December 2025 is already week 1 of 2026. Grouping by
    the plain calendar year would split that one week in two.
    """
    iso = moment.isocalendar()
    return iso.year, iso.week


def _newest_per_group(
    parsed: list[tuple[str, datetime]],
    group_of: Callable[[datetime], tuple[int, ...]],
    keep: int,
) -> set[str]:
    """Return the newest key of each of the ``keep`` most recent groups."""
    newest: dict[tuple[int, ...], tuple[str, datetime]] = {}
    for key, moment in parsed:
        label = group_of(moment)
        # "not in" tests a dict by its keys; a group seen for the first time is
        # stored, and a later backup of the same group replaces it only when it
        # is newer, so each group ends up holding its newest backup.
        if label not in newest or moment > newest[label][1]:
            newest[label] = (key, moment)

    # A set comprehension, read as "the key of each group label". Sorting the
    # labels with reverse=True puts the most recent group first, and the slice
    # keeps only that many.
    labels = sorted(newest, reverse=True)
    return {newest[label][0] for label in labels[:keep]}


def select_keys_to_delete(
    keys: list[str],
    keep_daily: int = KEEP_DAILY,
    keep_weekly: int = KEEP_WEEKLY,
    keep_monthly: int = KEEP_MONTHLY,
) -> list[str]:
    """Return the backup keys the retention rule no longer keeps (D96).

    The rule works on the keys alone, so nothing has to know today's date and a
    test needs no time travel. A key that does not parse is never returned:
    this never deletes a file it does not recognise.
    """
    parsed: list[tuple[str, datetime]] = []
    for key in keys:
        moment = parse_backup_time(key)
        if moment is None:
            continue
        parsed.append((key, moment))

    # Each window is one function that says how a moment is grouped, so the
    # same three-line rule below reads as the same sentence three times.
    keep: set[str] = set()
    keep |= _newest_per_group(parsed, _utc_day, keep_daily)
    keep |= _newest_per_group(parsed, _iso_week, keep_weekly)
    keep |= _newest_per_group(parsed, _utc_month, keep_monthly)

    # A list comprehension with an ``if``: another way to write a loop that
    # appends only some items.
    doomed = [(key, moment) for key, moment in parsed if key not in keep]
    # Oldest first, so a reader of the log sees them in the order they were
    # taken; the key breaks a tie, which only two identical timestamps make.
    doomed.sort(key=lambda pair: (pair[1], pair[0]))
    return [key for key, _moment in doomed]


def backup_bucket() -> str:
    """Return the backup bucket's name from the settings.

    The commands ask this function rather than reading the setting themselves,
    so exactly one module knows where a backup goes and the three of them can
    never disagree about the bucket.
    """
    return str(settings.BACKUP_R2_BUCKET)


def make_client() -> Any:
    """Build an S3 client for the backup bucket only (D96).

    These are the backup token's own values, never the media ones: a leaked
    token must not reach both the public photos and the backups.
    """
    bucket = backup_bucket()
    account_id = settings.BACKUP_R2_ACCOUNT_ID
    key_id = settings.BACKUP_R2_ACCESS_KEY_ID
    secret = settings.BACKUP_R2_SECRET_ACCESS_KEY

    required = {
        "BACKUP_R2_BUCKET": bucket,
        "BACKUP_R2_ACCOUNT_ID": account_id,
        "BACKUP_R2_ACCESS_KEY_ID": key_id,
        "BACKUP_R2_SECRET_ACCESS_KEY": secret,
    }
    # One refusal naming every blank setting, so the owner fixes them in one
    # edit instead of one restart per variable.
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ImproperlyConfigured(
            "Backups need these settings to be non-empty: " + ", ".join(missing) + "."
        )

    # ``getattr(object, name, default)`` reads an attribute and falls back to
    # the default when it does not exist. R2_BUCKET_NAME is set by prod.py only
    # when the media bucket is configured, so it is read this way rather than
    # assumed: the smaller change, and base.py stays as it is.
    media_bucket = getattr(settings, "R2_BUCKET_NAME", "")
    if bucket == media_bucket:
        raise ImproperlyConfigured(
            "BACKUP_R2_BUCKET must not be the media bucket (R2_BUCKET_NAME): "
            "backups never share a bucket or a token with the public photos."
        )

    return boto3.client(
        "s3",
        # R2 speaks the S3 API at this per-account address; "auto" is the only
        # region name it accepts, and s3v4 is the only signing it accepts.
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        region_name="auto",
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        config=Config(signature_version="s3v4"),
    )


def list_backup_keys(client: Any, bucket: str) -> list[str]:
    """Return every key under ``BACKUP_PREFIX`` in the bucket."""
    # A paginator turns one call that returns at most 1000 objects into a loop
    # that keeps asking for the next page until there are no more.
    paginator = client.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=BACKUP_PREFIX):
        # ``page.get("Contents", [])`` is an empty list when a page holds no
        # objects at all, which S3 does for an empty prefix.
        for item in page.get("Contents", []):
            keys.append(item["Key"])
    return keys
