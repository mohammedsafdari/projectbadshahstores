"""The only place AuditLog rows are written (ADR D44).

Callers (admin save_model, CSV import, bulk price update) describe the change
by passing the old and new field values; this module writes one AuditLog row
when at least one value changed.
"""

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.contrib.auth.base_user import AbstractBaseUser
from django.db import models

from apps.core.models import AuditLog


class AuditError(ValueError):
    """Raised when an audit entry is invalid."""


def snapshot(instance: models.Model, fields: Iterable[str]) -> dict[str, Any]:
    """Return {field_name: getattr(instance, field_name)} for each name, in order.

    ``getattr`` reads an attribute whose name is held in a string, so callers can
    pass field names that they only know at runtime.
    """
    return {field: getattr(instance, field) for field in fields}


def _clean_value(value: object, *, field: str) -> Any:
    """Normalise a value for JSON, raising AuditError for unsupported types.

    JSON has no Decimal type, so ``Decimal`` values become strings like
    ``"12.50"`` so the stored row is stable and round-trippable. Floats are
    rejected on purpose (the same reason as D43): money and quantities must stay
    exact, and a float would lose that exactness the moment it is stored.
    """
    # bool is a subclass of int, but we only need to accept it as-is, so a
    # single isinstance check for int is enough where it comes after None and
    # str. We pass booleans through unchanged so is_active changes read clearly.
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise AuditError(
        f"Field {field!r} has unsupported type {type(value).__name__!r}; "
        "only None, bool, int, str, Decimal, date and datetime are allowed."
    )


def record_change(
    *,
    actor: AbstractBaseUser | None,
    action: str,
    target: models.Model,
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    note: str = "",
) -> AuditLog | None:
    """Write one AuditLog row when at least one value changed, else return None.

    Call it inside the same ``transaction.atomic()`` block as the change it
    describes, so both succeed or both fail. Django's admin already wraps a
    model save in a transaction, so calling ``record_change`` from
    ``save_model`` is safe there.
    """
    action = action.strip()
    if not action:
        raise AuditError("action must not be empty.")
    if len(action) > 40:
        raise AuditError("action must be at most 40 characters.")

    note = note.strip()
    if len(note) > 200:
        raise AuditError("note must be at most 200 characters.")

    if target.pk is None:
        raise AuditError("target must already exist (target.pk is None).")

    target_type = target._meta.label_lower
    target_id = str(target.pk)

    # Only keep fields that are present in both old and new and whose value
    # actually differs. Comparison uses != on the raw values, so Decimal("12.5")
    # and Decimal("12.50") compare equal (same value, different display).
    changed: dict[str, list[Any]] = {}
    for key in new:
        if key not in old:
            raise AuditError(
                f"Field {key!r} is in new but not in old; old must contain every "
                "field being audited."
            )
        if old[key] != new[key]:
            changed[key] = [_clean_value(old[key], field=key), _clean_value(new[key], field=key)]

    if not changed:
        return None

    return AuditLog.objects.create(
        actor_id=actor.pk if actor is not None else None,
        action=action,
        target_type=target_type,
        target_id=target_id,
        changes=changed,
        note=note,
    )
