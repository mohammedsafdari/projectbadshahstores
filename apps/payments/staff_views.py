"""Refund actions for staff (D79 amendment).

"Send refund now" asks the provider again, and retries a refund the provider
refused. "Mark as refunded by hand" records money the shop paid outside the
gateway, with the actor, the time and a required note. Both actions need
``REFUND_PAYMENTS`` and never touch money unless the button was pressed.
"""

from __future__ import annotations

from typing import cast

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.accounts.capabilities import Capability
from apps.accounts.models import User
from apps.orders.services import OrderNotFoundError
from apps.orders.staff_queries import get_staff_order
from apps.orders.staff_views import staff_required
from apps.payments.models import Refund
from apps.payments.refunds import (
    RefundError,
    latest_refund,
    mark_refunded_by_hand,
    retry_refund,
    send_refund,
)

NO_REFUND_MESSAGE = _("This order has no refund to send.")
REFUND_SENT_MESSAGE = _("The refund was sent to the provider.")
REFUND_PROCESSED_MESSAGE = _("The refund is processed.")
REFUND_PENDING_MESSAGE = _("The refund was not completed yet. Try again.")
REFUND_FAILED_MESSAGE = _("The refund failed. Mark it as paid by hand if you paid it yourself.")
REFUND_BY_HAND_MESSAGE = _("The refund was marked as paid by hand.")
REFUND_ALREADY_SENT_MESSAGE = _("This refund was already sent to the provider.")


def _refund_result_message(status: str) -> str:
    """One plain sentence for the refund status the service returned."""
    if status == Refund.Status.PROCESSED:
        return str(REFUND_PROCESSED_MESSAGE)
    if status == Refund.Status.SENT:
        return str(REFUND_SENT_MESSAGE)
    if status == Refund.Status.FAILED:
        return str(REFUND_FAILED_MESSAGE)
    return str(REFUND_PENDING_MESSAGE)


@staff_required(Capability.REFUND_PAYMENTS)
@require_POST
@never_cache
def send_refund_now(request: HttpRequest, order_number: str) -> HttpResponse:
    """Send one refund now, retrying a FAILED refund (D79)."""
    user = cast(User, request.user)
    try:
        order = get_staff_order(user, order_number, capability=Capability.REFUND_PAYMENTS)
    except OrderNotFoundError:
        raise Http404("Order not found.") from None
    refund = latest_refund(order)
    if refund is None:
        messages.error(request, NO_REFUND_MESSAGE)
        return redirect("staff_order_detail", order_number=order.order_number)
    if refund.status == Refund.Status.SENT:
        # The provider may already have the money; sending again could refund
        # it twice. A person must check the provider instead (Slice 42c).
        messages.info(request, REFUND_ALREADY_SENT_MESSAGE)
        return redirect("staff_order_detail", order_number=order.order_number)
    try:
        # A FAILED refund is never retried automatically (D78), so this
        # explicit staff action resets it first and then sends it.
        stored = (
            retry_refund(refund.pk)
            if refund.status == Refund.Status.FAILED
            else send_refund(refund.pk)
        )
    except RefundError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, _refund_result_message(stored.status))
    return redirect("staff_order_detail", order_number=order.order_number)


@staff_required(Capability.REFUND_PAYMENTS)
@require_POST
@never_cache
def mark_refunded_by_hand_view(request: HttpRequest, order_number: str) -> HttpResponse:
    """Record money the shop paid back outside the gateway (D79)."""
    user = cast(User, request.user)
    try:
        order = get_staff_order(user, order_number, capability=Capability.REFUND_PAYMENTS)
    except OrderNotFoundError:
        raise Http404("Order not found.") from None
    refund = latest_refund(order)
    if refund is None:
        messages.error(request, NO_REFUND_MESSAGE)
        return redirect("staff_order_detail", order_number=order.order_number)
    try:
        mark_refunded_by_hand(
            refund.pk,
            actor=user,
            note=request.POST.get("note", ""),
        )
    except RefundError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, REFUND_BY_HAND_MESSAGE)
    return redirect("staff_order_detail", order_number=order.order_number)
