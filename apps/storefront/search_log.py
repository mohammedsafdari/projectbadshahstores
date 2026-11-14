"""The private log of search words (D89).

One row per cleaned word per Indian day, with two counters. Nothing identifies
the visitor, and a failure here never breaks a search: the page has already
found its products by the time this runs.
"""

from __future__ import annotations

import logging
import re

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.storefront.models import SearchTermDay

logger = logging.getLogger(__name__)

# A shorter word says nothing useful and would fill the table with noise.
MIN_TERM_LENGTH = 3
# The column width, and the cut applied before the checks below.
MAX_TERM_LENGTH = 60

# Six digits in a row is a phone number or a code, never a useful search word.
# The same rule the notification log uses for an error message (D88).
LONG_DIGIT_RUN = re.compile(r"\d{6,}")


def clean_term(raw: str) -> str | None:
    """The word to count, or None when it must not be counted (D89).

    The text is lower-cased, every run of whitespace becomes one space and the
    result is cut to the column width. A word shorter than three characters, a
    long run of digits and anything holding "@" (an email address) is refused.
    """
    # " ".join(text.split()) collapses and trims the spaces in one step.
    term = " ".join((raw or "").lower().split())[:MAX_TERM_LENGTH]
    if len(term) < MIN_TERM_LENGTH:
        return None
    if LONG_DIGIT_RUN.search(term):
        return None
    if "@" in term:
        return None
    return term


def record_search(raw_query: str, result_count: int) -> None:
    """Count one search for today, and never raise (D89).

    Two searches at the same moment must both count, so the counters are raised
    with ``F()`` expressions: the database adds one to the stored value instead
    of Python writing back a number it read a moment ago.
    """
    term = clean_term(raw_query)
    if term is None:
        return
    try:
        with transaction.atomic():
            row, _created = SearchTermDay.objects.get_or_create(
                day=timezone.localdate(),
                term=term,
            )
            counts: dict[str, object] = {"searches": F("searches") + 1}
            if result_count == 0:
                counts["empty_searches"] = F("empty_searches") + 1
            # ``update`` writes a relative value, and updated_at is set here
            # because a queryset update skips the auto_now field.
            SearchTermDay.objects.filter(pk=row.pk).update(
                updated_at=timezone.now(),
                **counts,
            )
    except Exception:
        # The search already worked; only the count was lost. The word itself
        # is the one thing this log may keep, so the line carries no query.
        logger.warning("Search word could not be recorded")
