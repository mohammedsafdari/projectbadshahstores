"""Storefront app models: the private log of search words (D89).

The storefront has no migrations folder, so the owner runs
``docker compose exec web python manage.py makemigrations storefront`` once.
"""

from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel


class SearchTermDay(TimeStampedModel):
    """How often one cleaned search word was typed on one Indian day (D89).

    The row never holds a user, an IP address or a session: it answers "which
    words do customers type, and which find nothing" and nothing else.
    """

    day = models.DateField()
    term = models.CharField(max_length=60)
    searches = models.PositiveIntegerField(default=0)
    empty_searches = models.PositiveIntegerField(default=0)

    class Meta:
        # Newest day first, and the words in alphabetical order inside a day.
        ordering = ["-day", "term"]
        constraints = [
            # One row per word per day, so a counter can be raised in place.
            models.UniqueConstraint(
                fields=["day", "term"],
                name="searchtermday_unique_day_term",
            ),
            # A word cannot have found nothing more often than it was searched.
            models.CheckConstraint(
                condition=models.Q(empty_searches__lte=models.F("searches")),
                name="searchtermday_empty_lte_searches",
            ),
        ]
        indexes = [
            models.Index(fields=["day"], name="searchtermday_day_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.day} {self.term}"
