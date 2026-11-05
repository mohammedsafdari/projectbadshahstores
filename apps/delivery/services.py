"""Delivery app business logic: pin-code serviceability and the delivery day (D62).

Everything here is a plain function: views, the admin and the account screens
call these instead of building the rules themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.services import get_shop_settings
from apps.delivery.models import PINCODE_VALIDATOR, DeliveryZone, PinCodeZone

if TYPE_CHECKING:
    # Imported only for the type checker. A runtime import here would make a
    # circle once accounts calls delivery (D62).
    from apps.accounts.models import Address


class InvalidPincodeError(ValueError):
    """Raised when a pin code is not six digits and does not start with 0."""


def normalise_pincode(raw: str) -> str:
    """Strip spaces and return the six digits, or raise InvalidPincodeError.

    Spaces inside the number are removed too, so "452 012" typed on a phone
    still works. Letters, signs and a wrong length are refused.
    """
    if not isinstance(raw, str):
        raise InvalidPincodeError("Enter a six-digit pin code.")
    # split() splits on any whitespace and drops the empty pieces, so joining
    # it back removes every space, tab and newline in one step.
    cleaned = "".join(raw.split())
    try:
        PINCODE_VALIDATOR(cleaned)
    except ValidationError as exc:
        raise InvalidPincodeError("Enter a six-digit pin code.") from exc
    return cleaned


def is_pincode_served(pincode: str) -> bool:
    """True when the pin exists and its zone is active; invalid input is False.

    A customer typing nonsense must never see an error page, so this function
    answers False instead of raising. It costs one query.
    """
    try:
        value = normalise_pincode(pincode)
    except InvalidPincodeError:
        return False
    return PinCodeZone.objects.filter(pincode=value, zone__is_active=True).exists()


def get_zone_for_pincode(pincode: str) -> DeliveryZone | None:
    """The active zone serving this pin, or None for an unknown or invalid pin."""
    try:
        value = normalise_pincode(pincode)
    except InvalidPincodeError:
        return None
    return DeliveryZone.objects.filter(pin_codes__pincode=value, is_active=True).first()


def address_is_served(address: Address) -> bool:
    """Whether a saved address's pin code is served (used by Slice 36)."""
    return is_pincode_served(address.pincode)


@dataclass(frozen=True)
class DeliveryPromise:
    """The day a customer is promised, already formatted for display."""

    day: date
    is_same_day: bool
    label: str
    cutoff: time


@dataclass(frozen=True)
class DeliveryCheck:
    """The answer to one "check delivery" request.

    ``pincode`` is the normalised pin for valid input and None otherwise, so
    the original text (which may be nonsense) is never carried back to a page.
    """

    pincode: str | None
    valid: bool
    served: bool
    zone_name: str | None
    promise: DeliveryPromise | None


def get_delivery_promise(now: datetime | None = None) -> DeliveryPromise:
    """Today before the cutoff, otherwise tomorrow morning (D55 amendment, D62).

    ``now`` must be aware. It is converted to the shop time zone (D4) before
    the cutoff is compared, so a UTC timestamp cannot shift the answer.
    """
    if now is None:
        now = timezone.now()
    if timezone.is_naive(now):
        raise ValueError("get_delivery_promise needs an aware datetime.")
    local_now = timezone.localtime(now)
    cutoff = get_shop_settings().same_day_cutoff
    if local_now.time() < cutoff:
        # Strictly before the cutoff: the shop still delivers today.
        day = local_now.date()
        is_same_day = True
        label = str(_("Today"))
    else:
        # At the cutoff or later: the first possible day is tomorrow.
        day = local_now.date() + timedelta(days=1)
        is_same_day = False
        label = str(_("Tomorrow morning"))
    return DeliveryPromise(day=day, is_same_day=is_same_day, label=label, cutoff=cutoff)


def check_delivery(raw_pincode: str | None, now: datetime | None = None) -> DeliveryCheck:
    """Check one pin and describe what the customer should be told."""
    if not raw_pincode:
        return DeliveryCheck(pincode=None, valid=False, served=False, zone_name=None, promise=None)
    try:
        pincode = normalise_pincode(raw_pincode)
    except InvalidPincodeError:
        return DeliveryCheck(pincode=None, valid=False, served=False, zone_name=None, promise=None)

    zone = get_zone_for_pincode(pincode)
    if zone is None:
        # The pin is well formed, so it is "unserved", not "invalid".
        return DeliveryCheck(
            pincode=pincode,
            valid=True,
            served=False,
            zone_name=None,
            promise=None,
        )
    return DeliveryCheck(
        pincode=pincode,
        valid=True,
        served=True,
        zone_name=zone.name,
        promise=get_delivery_promise(now),
    )
