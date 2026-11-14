"""Read-only admin for the notification log (D88).

A person may read the send history; nobody may add, change or delete a row,
because the log is the record of what really happened.
"""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from apps.notifications.models import NotificationLog


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    """The send history, read only (D88)."""

    list_display = (
        "created_at",
        "channel",
        "purpose",
        "event",
        "status",
        "attempts",
        "order",
    )
    list_filter = ("channel", "purpose", "status")
    search_fields = ("event", "provider_ref", "order__order_number")
    date_hierarchy = "created_at"
    ordering = ("-created_at", "-pk")

    # The three refusals below are the whole read-only rule: with change off,
    # Django shows a plain detail page with no Save button.
    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: NotificationLog | None = None
    ) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: NotificationLog | None = None
    ) -> bool:
        return False
