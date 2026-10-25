"""Django admin for the core app: a read-only view of the audit log."""

from collections.abc import Mapping, Sequence

from django.contrib import admin

from .models import AuditLog


# Mapping and Sequence are read-only types, so a dict[str, list[str]] is
# accepted too. A dict[str, list[object]] would not accept it, because dict
# and list are invariant.
def format_changes(changes: Mapping[str, Sequence[object]]) -> str:
    """Turn ``{field: [old, new]}`` into one text line for the admin list.

    Fields appear in stored order, separated by ``; ``. Each field is shown as
    ``field: old -> new`` using two ASCII characters ``->``. An empty dict
    returns ``-``.
    """
    if not changes:
        return "-"
    parts = []
    for field, pair in changes.items():
        old_value, new_value = pair
        parts.append(f"{field}: {old_value} -> {new_value}")
    text = "; ".join(parts)
    if len(text) > 120:
        text = text[:117] + "..."
    return text


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "action",
        "target_type",
        "target_id",
        "actor",
        "changes_summary",
    )
    list_filter = ("action", "target_type", "created_at")
    search_fields = ("target_id", "note", "actor__phone")
    # One query for the actors instead of one per row.
    list_select_related = ("actor",)
    list_per_page = 50

    @admin.display(description="Changes")
    def changes_summary(self, obj: AuditLog) -> str:
        return format_changes(obj.changes)

    # With change permission off, Django shows the entry as read-only and
    # removes the bulk "delete selected" action. Users with the view
    # permission can still open and read the entry.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]
