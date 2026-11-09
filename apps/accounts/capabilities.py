"""Staff capabilities and their default matrix (D75).

A capability is one thing a staff member may do. The role field on ``User`` is
only a label until this module turns it into rights: a stored ``RoleCapability``
row for the role overrides the default below, and the owner can change those
rows from the admin matrix.

Nothing here grants Django admin access. ``is_staff`` still decides who may log
into ``/admin/`` at all (D72); a capability decides what a staff member may do
inside the screens this project checks.
"""

from __future__ import annotations

from django.db.models import TextChoices

# The role strings are repeated here so this module can be imported by the
# model that stores capability rows without a circular import.
ROLE_OWNER = "OWNER"
ROLE_MANAGER = "MANAGER"
ROLE_PACKER = "PACKER"
ROLE_DELIVERY = "DELIVERY"
ROLE_MEMBER = "MEMBER"

# The five roles, in the order the owner matrix shows them. The owner is not
# editable: it always has every capability.
MATRIX_ROLES = (ROLE_MANAGER, ROLE_PACKER, ROLE_DELIVERY, ROLE_MEMBER)


class Capability(TextChoices):
    """One action a staff member may be allowed to take."""

    VIEW_ORDERS = "VIEW_ORDERS", "View orders"
    START_PACKING = "START_PACKING", "Start packing"
    SEND_OUT = "SEND_OUT", "Send out for delivery"
    DELIVER_COD = "DELIVER_COD", "Deliver a cash-on-delivery order"
    STAFF_CANCEL = "STAFF_CANCEL", "Cancel an order as staff"
    RECORD_CREDIT_PAYMENT = "RECORD_CREDIT_PAYMENT", "Record a credit payment"
    VIEW_CREDIT_DUES = "VIEW_CREDIT_DUES", "View credit dues"
    STOCK_WRITE = "STOCK_WRITE", "Receive, adjust, count or damage stock"
    AUDIT_READ = "AUDIT_READ", "Read the audit log"
    EXPORT_CSV = "EXPORT_CSV", "Export variants to CSV"


# The default rights of each role, used until the owner stores an override.
# A frozenset holds unique values and is cheap to test with ``in``.
DEFAULTS: dict[str, frozenset[str]] = {
    ROLE_OWNER: frozenset(Capability.values),
    ROLE_MANAGER: frozenset(Capability.values),
    ROLE_PACKER: frozenset(
        {
            Capability.VIEW_ORDERS,
            Capability.START_PACKING,
            Capability.SEND_OUT,
        }
    ),
    ROLE_DELIVERY: frozenset({Capability.DELIVER_COD}),
    ROLE_MEMBER: frozenset(),
}


def default_allowed(role: str, capability: str) -> bool:
    """What this role may do when no stored row overrides it."""
    return capability in DEFAULTS.get(role, frozenset())


def has_capability(user, capability: str) -> bool:
    """True when this user may take this action right now.

    Guards run before any database read: an anonymous, inactive or non-staff
    user has no capabilities. An active staff superuser or OWNER always passes,
    so the owner can never lock themselves out. Every other staff member uses
    the stored row for their role, or the default when no row exists.

    The prompt requires exactly one query per call and no caching, so the row is
    read on every call instead of being held on the user or in a module cache.
    """
    # Imported here, not at module level: models.py imports Capability, so a
    # top-level import of the models would be circular.
    from apps.accounts.models import RoleCapability, User

    if capability not in Capability.values:
        raise ValueError(f"Unknown capability {capability!r}.")
    if not isinstance(user, User) or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser or user.role == ROLE_OWNER:
        return True

    stored = RoleCapability.objects.filter(
        role=user.role,
        capability=capability,
    ).first()
    if stored is not None:
        return stored.allowed
    return default_allowed(user.role, capability)
