"""Write one role's capability overrides, with one audit row per change (D75).

The admin matrix never writes a ``RoleCapability`` row itself. It builds the
whole posted state and this service keeps only the cells that really differ
from the current effective value. That way an unchanged submit creates no row,
writes no audit entry and does not move ``updated_at``.
"""

from __future__ import annotations

from collections.abc import Mapping

from django.db import transaction

from apps.accounts.capabilities import (
    MATRIX_ROLES,
    ROLE_OWNER,
    Capability,
    default_allowed,
)
from apps.accounts.models import RoleCapability, User
from apps.core.audit import record_change

# One action name for every cell change, so the audit list groups them (D44).
ROLE_CAPABILITY_ACTION = "ROLE_CAPABILITY_CHANGED"

# The note stored on every audit row this service writes.
ROLE_CAPABILITY_NOTE = "Role capability (admin)"


class RoleCapabilityError(ValueError):
    """The actor, role or posted value cannot change capabilities."""


def _require_editor(actor: User | None) -> User:
    """Only an active staff superuser or OWNER may change the matrix."""
    if not isinstance(actor, User):
        raise RoleCapabilityError("Only the owner can change capabilities.")
    if not actor.is_active or not actor.is_staff:
        raise RoleCapabilityError("Only the owner can change capabilities.")
    if not (actor.is_superuser or actor.role == ROLE_OWNER):
        raise RoleCapabilityError("Only the owner can change capabilities.")
    return actor


def set_role_capabilities(
    actor: User | None,
    role: str,
    changes: Mapping[str, bool],
) -> list[str]:
    """Store the posted cells of one role and return the capabilities changed.

    ``changes`` holds every capability of the matrix and its posted value, so a
    cleared checkbox arrives as ``False`` rather than being missing. A capability
    whose posted value equals the current effective value is left alone.
    """
    editor = _require_editor(actor)
    if role == ROLE_OWNER:
        raise RoleCapabilityError("The owner always has every capability.")
    if role not in MATRIX_ROLES:
        raise RoleCapabilityError(f"Unknown role {role!r}.")

    checked: dict[str, bool] = {}
    for capability, allowed in changes.items():
        if capability not in Capability.values:
            raise RoleCapabilityError(f"Unknown capability {capability!r}.")
        if not isinstance(allowed, bool):
            raise RoleCapabilityError("A capability value must be true or false.")
        checked[capability] = allowed

    changed: list[str] = []
    with transaction.atomic():
        # Lock the editor's user row first, the same first lock as place_order
        # and the credit services, so two matrix submits queue instead of racing.
        User.objects.select_for_update().get(pk=editor.pk)

        # Iterate the enum, not the posted dict, so writes and audit rows keep
        # one stable order whatever order the browser sent.
        for capability in Capability.values:
            if capability not in checked:
                continue
            allowed = checked[capability]
            row = (
                RoleCapability.objects.select_for_update()
                .filter(role=role, capability=capability)
                .first()
            )
            old = row.allowed if row is not None else default_allowed(role, capability)
            if old == allowed:
                continue

            if row is None:
                # A new cell stores the role's default as the audit's old value.
                row = RoleCapability.objects.create(
                    role=role,
                    capability=capability,
                    allowed=allowed,
                    updated_by=editor,
                )
            else:
                row.allowed = allowed
                row.updated_by = editor
                row.save(update_fields=["allowed", "updated_by", "updated_at"])

            record_change(
                actor=editor,
                action=ROLE_CAPABILITY_ACTION,
                target=row,
                old={"allowed": old},
                new={"allowed": allowed},
                note=ROLE_CAPABILITY_NOTE,
            )
            changed.append(capability)

    return changed
