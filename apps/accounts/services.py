"""Accounts app business logic: the customer address book (D57).

Every function takes the user first and filters on that user, so one customer
can never read or change another customer's address by guessing an id. Views and
forms must call these functions instead of using ``Address.objects`` directly.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from apps.accounts.models import Address, User
from apps.core.phone import normalise_phone

# A customer may keep this many active addresses. It is policy, not a column
# and not a database constraint, so the owner's answer can change it (D57).
MAX_ACTIVE_ADDRESSES = 5

# The fields a caller may set. ``user`` is the first argument, ``is_default`` is
# decided by the service, and ``is_active`` changes only through archive.
WRITABLE_ADDRESS_FIELDS: tuple[str, ...] = (
    "recipient_name",
    "recipient_phone",
    "line1",
    "line2",
    "landmark",
    "city",
    "state",
    "pincode",
    "label",
)


class AddressError(ValueError):
    """Raised when an address request is refused."""


class AddressNotFound(AddressError):
    """Raised when the address does not exist for this user."""


class AddressLimitReached(AddressError):
    """Raised when the user already has the maximum number of active addresses."""


def list_addresses(user: User) -> list[Address]:
    """The user's active addresses: default first, then oldest first (D57)."""
    # ``-is_default`` puts True (the default) before False; ``pk`` is the
    # oldest-first tiebreaker, because a lower pk was inserted earlier.
    return list(Address.objects.filter(user=user, is_active=True).order_by("-is_default", "pk"))


def get_address(user: User, pk: int) -> Address:
    """One address of this user; another user's address is "not found" (D57)."""
    try:
        # Both filters matter: pk alone would let a customer read someone else's
        # address, which is the object-level check this service exists for.
        return Address.objects.get(user=user, pk=pk)
    except Address.DoesNotExist:
        # ``from None`` hides Django's internal exception from the caller.
        raise AddressNotFound("Address not found.") from None


def create_address(user: User, **fields: Any) -> Address:
    """Create one address, making it the default when it is the first one.

    ``**fields`` collects the keyword arguments into a dict; the caller passes
    ``recipient_name="..."`` and so on, exactly like a model constructor.
    """
    data = _prepare_fields(fields)
    with transaction.atomic():
        # Lock the user row so two simultaneous "first" addresses cannot both
        # see "no default yet" and both become the default (D57).
        User.objects.select_for_update().get(pk=user.pk)
        active_count = Address.objects.filter(user=user, is_active=True).count()
        if active_count >= MAX_ACTIVE_ADDRESSES:
            raise AddressLimitReached(
                f"A customer may keep at most {MAX_ACTIVE_ADDRESSES} active addresses."
            )
        address = Address(user=user, **data)
        has_default = Address.objects.filter(user=user, is_active=True, is_default=True).exists()
        if not has_default:
            address.is_default = True
        address.full_clean()
        address.save()
        return address


def update_address(user: User, pk: int, **fields: Any) -> Address:
    """Change some fields of one address of this user."""
    data = _prepare_fields(fields)
    with transaction.atomic():
        address = get_address(user, pk)
        if not data:
            return address
        # setattr takes the attribute name as a string, so the dict of fields
        # can be applied in one loop.
        for name, value in data.items():
            setattr(address, name, value)
        address.full_clean()
        # auto_now writes updated_at only when it is named in update_fields; a
        # dict iterates over its keys, so [*data, "updated_at"] lists them all.
        address.save(update_fields=[*data, "updated_at"])
        return address


def archive_address(user: User, pk: int) -> Address:
    """Archive one address; archiving the default promotes the oldest other one."""
    with transaction.atomic():
        address = get_address(user, pk)
        if not address.is_active:
            # Already archived: nothing to do, and no second timestamp change.
            return address
        was_default = address.is_default
        address.is_active = False
        address.is_default = False
        address.save(update_fields=["is_active", "is_default", "updated_at"])
        if was_default:
            # The lowest pk is the oldest remaining active address, the same
            # order the customer sees in list_addresses.
            replacement = Address.objects.filter(user=user, is_active=True).order_by("pk").first()
            if replacement is not None:
                replacement.is_default = True
                replacement.save(update_fields=["is_default", "updated_at"])
        return address


def set_default_address(user: User, pk: int) -> Address:
    """Make one active address the user's only default."""
    with transaction.atomic():
        address = get_address(user, pk)
        if not address.is_active:
            raise AddressError("An archived address cannot be the default.")
        if address.is_default:
            return address
        # Clear the old default in the same transaction, so the partial unique
        # constraint never sees two defaults at once (D57).
        previous = Address.objects.select_for_update().filter(user=user, is_default=True)
        for other in previous:
            other.is_default = False
            other.save(update_fields=["is_default", "updated_at"])
        address.is_default = True
        address.save(update_fields=["is_default", "updated_at"])
        return address


def _prepare_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Refuse unknown names and normalise the phone, so all writes share one path."""
    unknown = sorted(set(fields) - set(WRITABLE_ADDRESS_FIELDS))
    if unknown:
        raise AddressError(f"Unknown address field(s): {', '.join(unknown)}.")
    data = dict(fields)
    # Only touch the phone when the caller supplied it: an update that does not
    # mention the phone must keep the stored value.
    if "recipient_phone" in data:
        try:
            data["recipient_phone"] = normalise_phone(str(data["recipient_phone"]))
        except ValueError as exc:
            raise AddressError(str(exc)) from exc
    return data
