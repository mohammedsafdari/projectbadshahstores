"""Django admin for the core app: the audit log and the shop settings."""

from collections.abc import Mapping, Sequence
from typing import Any

from django.contrib import admin
from django.contrib.auth.base_user import AbstractBaseUser
from django.core.exceptions import PermissionDenied
from django.forms import ModelForm
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse

from apps.accounts.capabilities import Capability, has_capability

from .audit import (
    AUDITED_SHOP_SETTINGS_FIELDS,
    record_shop_settings_change,
    snapshot_shop_settings_from_db,
)
from .models import AuditLog, ShopSettings


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


def _audit_actor(request: HttpRequest) -> AbstractBaseUser | None:
    """The logged-in user behind a request, or None for anything else.

    The isinstance check narrows the type from "user or anonymous user" to a
    real user for the type checker.
    """
    user = getattr(request, "user", None)
    return user if isinstance(user, AbstractBaseUser) else None


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
    def has_view_permission(self, request, obj=None):
        # The role capability is checked as well as Django's own view
        # permission (D75); an active superuser passes through has_capability.
        return has_capability(request.user, Capability.AUDIT_READ) and super().has_view_permission(
            request, obj
        )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]


@admin.register(ShopSettings)
class ShopSettingsAdmin(admin.ModelAdmin):
    """The one-row settings page: no add, no delete, audited saves (D58)."""

    list_display = ("shop_name", "phone", "is_published", "updated_at")
    readonly_fields = ("created_at", "updated_at")

    # A second row would make "the settings" ambiguous, so the admin cannot add
    # one; the row must stay for the site to work, so it cannot be deleted.
    def has_add_permission(self, request) -> bool:
        return False

    def changelist_view(
        self,
        request: HttpRequest,
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        """There is one row, so skip the list and open its change page.

        ``ShopSettings.load()`` creates the row on a fresh install, which means
        the first staff visit works without a data migration. The permission
        check stays explicit: opening this URL needs the change permission.
        """
        if not self.has_change_permission(request):
            raise PermissionDenied
        row = ShopSettings.load()
        # reverse() builds the admin URL from the model and pk, never by hand.
        return HttpResponseRedirect(reverse("admin:core_shopsettings_change", args=[row.pk]))

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def save_model(
        self,
        request: HttpRequest,
        obj: ShopSettings,
        form: ModelForm,
        change: bool,
    ) -> None:
        # Read the stored values BEFORE the save: the form has already put the
        # new values on ``obj``, so only the database still has the old ones.
        old = snapshot_shop_settings_from_db()
        # ``update_fields`` writes only the named columns, and auto_now touches
        # ``updated_at`` only when it is named, so list it too (D44, D58).
        obj.save(update_fields=[*AUDITED_SHOP_SETTINGS_FIELDS, "updated_at"])
        record_shop_settings_change(
            actor=_audit_actor(request),
            settings_row=obj,
            old=old,
            note="Shop settings (admin)",
        )
