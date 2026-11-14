"""The one template tag the staff header needs for the Reports link (D85).

The header is shared by every staff screen, and those screens do not all read
the report capabilities into their context, so the link asks for the right here.
``has_capability`` is the one rule (D75) and returns False for a guest.
"""

from __future__ import annotations

from django import template

from apps.accounts.capabilities import Capability, has_capability

register = template.Library()


@register.simple_tag
def can_view_reports(user: object) -> bool:
    """True when this signed-in user may open the reports."""
    return has_capability(user, Capability.VIEW_REPORTS)
