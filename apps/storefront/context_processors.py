"""The two values the header language form needs (D92).

Nothing here reads the database: one flag comes from a setting and the other
from the language Django has already activated for this request, so the query
budgets of D90 do not move.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest
from django.utils.translation import get_language

from apps.catalog.names import is_hindi


def language_switch(request: HttpRequest) -> dict[str, bool]:
    """Whether the switcher shows, and whether the page is already Hindi."""
    return {
        "hindi_enabled": bool(getattr(settings, "HINDI_ENABLED", False)),
        "hindi_active": is_hindi(get_language()),
    }
