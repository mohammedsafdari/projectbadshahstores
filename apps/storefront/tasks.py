"""Scheduled storefront jobs (D89).

One job so far: the search word log is deleted after its retention window, so
the table cannot grow for ever.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.storefront.models import SearchTermDay

logger = logging.getLogger(__name__)

# How many days of search words are kept.
RETENTION_DAYS = 180


@shared_task
def delete_old_search_terms() -> int:
    """Delete the search words older than the retention window (D89).

    Returns how many rows went. A day exactly ``RETENTION_DAYS`` old is kept:
    the filter is strictly older than the cutoff.
    """
    cutoff = timezone.localdate() - timedelta(days=RETENTION_DAYS)
    deleted, _details = SearchTermDay.objects.filter(day__lt=cutoff).delete()
    return deleted
