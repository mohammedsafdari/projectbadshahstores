"""Delivery app views: the public pin-code check (D62)."""

from __future__ import annotations

from datetime import time

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.delivery.services import DeliveryCheck, check_delivery

INVALID_PINCODE_MESSAGE = _("Enter a six-digit pin code.")
NOT_SERVED_MESSAGE = _("Sorry, we do not deliver to %(pincode)s yet.")
SERVED_TODAY_MESSAGE = _(
    "We deliver to %(pincode)s (%(zone)s). Order before %(cutoff)s and we deliver today."
)
SERVED_TOMORROW_MESSAGE = _(
    "We deliver to %(pincode)s (%(zone)s). Orders placed now are delivered tomorrow morning."
)


def _is_htmx(request: HttpRequest) -> bool:
    """The fixed rule: an htmx request sends exactly ``HX-Request: true``."""
    return request.headers.get("HX-Request") == "true"


def _format_cutoff(cutoff: time) -> str:
    """Format a time the way ``g:i A`` does: 17:00 becomes 5:00 PM."""
    hour = cutoff.hour % 12 or 12
    suffix = "AM" if cutoff.hour < 12 else "PM"
    return f"{hour}:{cutoff.minute:02d} {suffix}"


def _result_context(check: DeliveryCheck) -> dict[str, str]:
    """The wording and the accessible role for one result.

    Invalid input never reaches the message, so a script tag or a phone number
    typed into the box is not echoed back.
    """
    if not check.valid:
        return {"role": "alert", "message": str(INVALID_PINCODE_MESSAGE)}
    if not check.served:
        message = NOT_SERVED_MESSAGE % {"pincode": check.pincode or ""}
        return {"role": "alert", "message": str(message)}

    promise = check.promise
    if promise is None:
        # A served pin always has a promise; this branch only keeps the type
        # checker and a corrupt row from raising.
        return {"role": "alert", "message": str(INVALID_PINCODE_MESSAGE)}

    values = {
        "pincode": check.pincode or "",
        "zone": check.zone_name or "",
        "cutoff": _format_cutoff(promise.cutoff),
    }
    if promise.is_same_day:
        return {"role": "status", "message": str(SERVED_TODAY_MESSAGE % values)}
    return {"role": "status", "message": str(SERVED_TOMORROW_MESSAGE % values)}


@require_GET
@never_cache
def delivery_check(request: HttpRequest) -> HttpResponse:
    """Answer the public "check delivery" box with a fragment or a page."""
    check = check_delivery(request.GET.get("pincode"))
    context = _result_context(check)
    if _is_htmx(request):
        return render(request, "delivery/_check_result.html", context)
    return render(request, "delivery/check.html", context)
