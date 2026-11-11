"""Staff order screens under ``/staff/`` (D79 amendment).

Every view is wrapped by ``staff_required``: it first sends a guest to the
shop login with ``next``, then refuses a signed-in user without the role
capability with a plain 403 page (D75). The 42a services check the capability
again, so hiding a button is never the only guard.

The views stay thin: they read the query string, call a 42a service and render
a template. Every action error is shown as a plain message on the same page,
never as a crash page.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import wraps
from typing import Any, cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.capabilities import Capability, has_capability
from apps.accounts.models import User
from apps.inventory.services import InsufficientStockError
from apps.orders.models import Order
from apps.orders.presentation import money, timeline_time_text
from apps.orders.services import (
    CancelReasonRequiredError,
    CustomerNotVerifiedError,
    InvalidTransitionError,
    OrderNotFoundError,
)
from apps.orders.staff_queries import (
    cash_reconciliation,
    delivery_orders_for_day,
    get_staff_order,
    list_staff_orders,
    pick_list_for_day,
)
from apps.orders.staff_services import (
    STAFF_CANCELLABLE,
    StaffOrderError,
    StaffPermissionError,
    delivery_gate_reason,
    mark_delivered,
    pack_and_send_out,
    staff_cancel_order,
    start_packing,
)
from apps.payments.refunds import latest_refund, refund_is_stuck

LOGIN_URL = reverse_lazy("account_login")

# Every refusal a staff action may raise, shown as one plain sentence.
ACTION_ERRORS = (
    StaffOrderError,
    StaffPermissionError,
    CustomerNotVerifiedError,
    InvalidTransitionError,
    CancelReasonRequiredError,
)

# The shelf is short even though the hold was there. The staff member has to
# cancel the order or correct the stock; a partial send-out is not allowed.
SHORT_STOCK_MESSAGE = _(
    "The shelf count is lower than this order. Cancel the order or correct the stock, "
    "then try again."
)

# One place for the words each successful action shows.
PACKING_STARTED_MESSAGE = _("Packing started.")
SENT_OUT_MESSAGE = _("The order is out for delivery.")
DELIVERED_MESSAGE = _("The order was delivered and the cash was recorded.")
CANCELLED_MESSAGE = _("The order was cancelled.")


def staff_required(
    capability: str,
) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    """Require a signed-in staff member with this capability (D75).

    ``login_required`` runs first, so an anonymous visitor is sent to the shop
    login with ``next`` instead of seeing a 403 page. The capability check
    then runs before the view touches anything.
    """

    def decorator(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @wraps(view)
        @login_required(login_url=LOGIN_URL)
        def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if not has_capability(request.user, capability):
                return render(
                    request,
                    "staff/403.html",
                    {"capability": str(Capability(capability).label)},
                    status=403,
                )
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def staff_nav(user: User) -> dict[str, bool]:
    """The screens this user may open, for the staff header (D75)."""
    return {
        "can_view_orders": has_capability(user, Capability.VIEW_ORDERS),
        "can_deliver": has_capability(user, Capability.DELIVER_COD),
        "can_view_credit": has_capability(user, Capability.VIEW_CREDIT_DUES),
    }


def _current_user(request: HttpRequest) -> User:
    # staff_required has already refused a guest; the cast only tells mypy.
    return cast(User, request.user)


def _filter_day(value: str | None) -> date | None:
    """A ``YYYY-MM-DD`` filter value, or None when it is blank or invalid."""
    if not value:
        return None
    return parse_date(value)


def _day_or_today(value: str | None) -> date:
    """A chosen day, or today in the shop time zone when none was chosen."""
    if value:
        parsed = parse_date(value)
        if parsed is not None:
            return parsed
    return timezone.localdate()


def _status_text(value: str) -> str:
    """The plain label of one stored order status."""
    for choice, label in Order.Status.choices:
        if str(choice) == value:
            return str(label)
    return value


def _order_lines(order: Order) -> list[dict[str, str]]:
    """One small dict per frozen order line, so the template only prints values."""
    return [
        {
            "name": item.product_name,
            "sku": item.sku_code,
            "quantity": f"{item.quantity_ordered:g} x {item.pack_label}",
            "total": money(item.line_total),
        }
        for item in order.items.all()
    ]


def _action_flags(user: User, order: Order) -> dict[str, bool]:
    """Which action buttons this user may use for this order (D75, D79)."""
    return {
        "can_start_packing": (
            order.status == Order.Status.CONFIRMED
            and has_capability(user, Capability.START_PACKING)
        ),
        "can_send_out": (
            order.status == Order.Status.PACKING and has_capability(user, Capability.SEND_OUT)
        ),
        "can_cancel": (
            order.status in STAFF_CANCELLABLE and has_capability(user, Capability.STAFF_CANCEL)
        ),
    }


@login_required(login_url=LOGIN_URL)
@require_http_methods(["GET"])
def staff_home(request: HttpRequest) -> HttpResponse:
    """Send a staff member to the first screen their role opens."""
    user = _current_user(request)
    if has_capability(user, Capability.VIEW_ORDERS):
        return redirect("staff_order_list")
    if has_capability(user, Capability.DELIVER_COD):
        return redirect("staff_delivery_list")
    if has_capability(user, Capability.VIEW_CREDIT_DUES):
        return redirect("staff_credit_dues")
    return render(request, "staff/403.html", {}, status=403)


@staff_required(Capability.VIEW_ORDERS)
@require_http_methods(["GET"])
@never_cache
def order_list(request: HttpRequest) -> HttpResponse:
    """The staff order list with the 42a filters and search (D79)."""
    user = _current_user(request)
    status = request.GET.get("status") or None
    if status is not None and status not in Order.Status.values:
        status = None
    payment_method = request.GET.get("payment_method") or None
    if payment_method is not None and payment_method not in Order.PaymentMethod.values:
        payment_method = None
    search = (request.GET.get("q") or "").strip()
    rows = list_staff_orders(
        user,
        status=status,
        delivery_day=_filter_day(request.GET.get("day")),
        payment_method=payment_method,
        search=search,
    )
    # The capabilities are read once, not once per row: the page stays at a
    # constant number of queries however long the list is.
    can_start = has_capability(user, Capability.START_PACKING)
    can_send = has_capability(user, Capability.SEND_OUT)
    table = [
        {
            "order": row.order,
            "order_number": row.order_number,
            "status_text": _status_text(row.status),
            "customer_name": row.customer_name,
            "customer_phone": row.customer_phone,
            "total_text": money(row.grand_total),
            "cash_text": money(row.amount_due_on_delivery),
            "payment_text": row.order.get_payment_method_display(),
            "delivery_day": row.delivery_day,
            "gate_reason": row.gate_reason,
            "phone_verified": row.phone_verified,
            "email_verified": row.email_verified,
            "detail_url": reverse("staff_order_detail", args=[row.order_number]),
            "start_url": reverse("staff_start_packing", args=[row.order_number]),
            "send_url": reverse("staff_send_out", args=[row.order_number]),
            "can_start_packing": can_start and row.status == Order.Status.CONFIRMED,
            "can_send_out": can_send and row.status == Order.Status.PACKING,
        }
        for row in rows
    ]
    return render(
        request,
        "staff/order_list.html",
        {
            "rows": table,
            "search": search,
            "status": status or "",
            "payment_method": payment_method or "",
            "day": request.GET.get("day", "") or "",
            "status_choices": Order.Status.choices,
            "payment_choices": Order.PaymentMethod.choices,
            **_nav_for(user),
        },
    )


def _nav_for(user: User) -> dict[str, bool]:
    """A thin alias so the header flags have one name in every context."""
    return staff_nav(user)


@staff_required(Capability.VIEW_ORDERS)
@require_http_methods(["GET"])
@never_cache
def pick_list(request: HttpRequest) -> HttpResponse:
    """The pick list of one delivery day, with a print stylesheet (D79)."""
    user = _current_user(request)
    day = _day_or_today(request.GET.get("day"))
    result = pick_list_for_day(user, day)
    return render(
        request,
        "staff/pick_list.html",
        {
            "day": day,
            "pick_list": result,
            "orders": result.orders,
            "totals": result.totals,
            **_nav_for(user),
        },
    )


def _detail_context(user: User, order: Order, *, error: str | None = None) -> dict[str, Any]:
    """Every value the order detail page prints (D79)."""
    refund = latest_refund(order)
    flags = _action_flags(user, order)
    can_refund = has_capability(user, Capability.REFUND_PAYMENTS)
    refund_stuck = refund_is_stuck(refund)
    refund_open = refund is not None and (refund.status in ("REQUESTED", "FAILED") or refund_stuck)
    can_send = refund is not None and refund.status in ("REQUESTED", "FAILED") and can_refund
    can_mark = refund_open and can_refund
    return {
        "order": order,
        "customer": order.user,
        "status_text": _status_text(order.status),
        "delivery_day": order.promised_delivery_date,
        "lines": _order_lines(order),
        "logs": [
            {
                "label": _status_text(log.to_status),
                "time": timeline_time_text(log.created_at),
                "note": log.note,
            }
            for log in order.status_logs.all()
        ],
        "payments": list(order.payments.all()),
        "total_text": money(order.grand_total),
        "cash_text": money(order.amount_due_on_delivery),
        "credit_text": money(order.credit_applied),
        "online_text": money(order.online_amount),
        "gate_reason": delivery_gate_reason(order.user),
        "refund": refund,
        "refund_open": refund_open,
        "refund_stuck": refund_stuck,
        "can_send_refund": can_send,
        "can_mark_by_hand": can_mark,
        "slip_url": reverse("staff_packing_slip", args=[order.order_number]),
        "detail_url": reverse("staff_order_detail", args=[order.order_number]),
        "start_url": reverse("staff_start_packing", args=[order.order_number]),
        "send_url": reverse("staff_send_out", args=[order.order_number]),
        "cancel_url": reverse("staff_cancel", args=[order.order_number]),
        "refund_url": reverse("staff_send_refund", args=[order.order_number]),
        "by_hand_url": reverse("staff_mark_refunded_by_hand", args=[order.order_number]),
        "error": error,
        **flags,
        **_nav_for(user),
    }


@staff_required(Capability.VIEW_ORDERS)
@require_http_methods(["GET"])
@never_cache
def order_detail(request: HttpRequest, order_number: str) -> HttpResponse:
    """One order with its status log, lines, payments and actions (D79)."""
    user = _current_user(request)
    try:
        order = get_staff_order(user, order_number, capability=Capability.VIEW_ORDERS)
    except OrderNotFoundError:
        raise Http404("Order not found.") from None
    return render(request, "staff/order_detail.html", _detail_context(user, order))


@staff_required(Capability.VIEW_ORDERS)
@require_http_methods(["GET"])
def packing_slip(request: HttpRequest, order_number: str) -> HttpResponse:
    """A plain printable packing slip; it is not a tax invoice (D79)."""
    user = _current_user(request)
    try:
        order = get_staff_order(user, order_number, capability=Capability.VIEW_ORDERS)
    except OrderNotFoundError:
        raise Http404("Order not found.") from None
    return render(
        request,
        "staff/packing_slip.html",
        {"order": order, "lines": _order_lines(order)},
    )


def _render_detail(
    request: HttpRequest,
    user: User,
    order: Order,
    *,
    error: str,
) -> HttpResponse:
    """Show the detail page again with one plain refusal message (D79)."""
    return render(request, "staff/order_detail.html", _detail_context(user, order, error=error))


@staff_required(Capability.START_PACKING)
@require_POST
@never_cache
def start_packing_view(request: HttpRequest, order_number: str) -> HttpResponse:
    """Start packing a confirmed order (D79)."""
    user = _current_user(request)
    try:
        order = get_staff_order(user, order_number, capability=Capability.START_PACKING)
    except OrderNotFoundError:
        raise Http404("Order not found.") from None
    try:
        start_packing(order.pk, actor=user)
    except ACTION_ERRORS as error:
        return _render_detail(request, user, order, error=str(error))
    messages.success(request, PACKING_STARTED_MESSAGE)
    return redirect("staff_order_detail", order_number=order.order_number)


@staff_required(Capability.SEND_OUT)
@require_POST
@never_cache
def send_out_view(request: HttpRequest, order_number: str) -> HttpResponse:
    """Pack and send out a picking order, consuming its stock holds (D79)."""
    user = _current_user(request)
    try:
        order = get_staff_order(user, order_number, capability=Capability.SEND_OUT)
    except OrderNotFoundError:
        raise Http404("Order not found.") from None
    try:
        pack_and_send_out(order.pk, actor=user)
    except InsufficientStockError:
        # The hold existed, but the shelf no longer has the goods. No partial
        # send-out: the staff member cancels or corrects the stock (D79).
        return _render_detail(request, user, order, error=str(SHORT_STOCK_MESSAGE))
    except ACTION_ERRORS as error:
        return _render_detail(request, user, order, error=str(error))
    messages.success(request, SENT_OUT_MESSAGE)
    return redirect("staff_order_detail", order_number=order.order_number)


@staff_required(Capability.STAFF_CANCEL)
@require_POST
@never_cache
def cancel_view(request: HttpRequest, order_number: str) -> HttpResponse:
    """Cancel an order as staff; the reason is required (D79)."""
    user = _current_user(request)
    try:
        order = get_staff_order(user, order_number, capability=Capability.STAFF_CANCEL)
    except OrderNotFoundError:
        raise Http404("Order not found.") from None
    try:
        staff_cancel_order(
            order.pk,
            actor=user,
            reason=request.POST.get("reason", ""),
        )
    except ACTION_ERRORS as error:
        return _render_detail(request, user, order, error=str(error))
    messages.success(request, CANCELLED_MESSAGE)
    return redirect("staff_order_detail", order_number=order.order_number)


def _delivery_context(user: User, *, error: str | None = None) -> dict[str, Any]:
    """The delivery list of today, with the cash each order needs at the door."""
    orders = delivery_orders_for_day(user, timezone.localdate())
    return {
        "day": timezone.localdate(),
        "orders": orders,
        "error": error,
        **_nav_for(user),
    }


@staff_required(Capability.DELIVER_COD)
@require_http_methods(["GET"])
@never_cache
def delivery_list(request: HttpRequest) -> HttpResponse:
    """Every order out for delivery today, with its address and cash (D79)."""
    return render(request, "staff/delivery_list.html", _delivery_context(_current_user(request)))


@staff_required(Capability.DELIVER_COD)
@require_POST
@never_cache
def mark_delivered_view(request: HttpRequest, order_number: str) -> HttpResponse:
    """Record the cash at the door and mark the order delivered (D79)."""
    user = _current_user(request)
    try:
        order = get_staff_order(user, order_number, capability=Capability.DELIVER_COD)
    except OrderNotFoundError:
        raise Http404("Order not found.") from None

    raw_amount = (request.POST.get("collected_amount") or "").strip()
    amount: Decimal | None = None
    if raw_amount:
        try:
            amount = Decimal(raw_amount)
        except (InvalidOperation, ValueError):
            return render(
                request,
                "staff/delivery_list.html",
                _delivery_context(
                    user,
                    error=str(_("Enter the collected amount as a number.")),
                ),
            )
    try:
        mark_delivered(order.pk, actor=user, collected_amount=amount)
    except ACTION_ERRORS as error:
        return render(
            request,
            "staff/delivery_list.html",
            _delivery_context(user, error=str(error)),
        )
    messages.success(request, DELIVERED_MESSAGE)
    return redirect("staff_delivery_list")


@staff_required(Capability.DELIVER_COD)
@require_http_methods(["GET"])
@never_cache
def cash_page(request: HttpRequest) -> HttpResponse:
    """The cash collected on one day, and the orders that still owe cash (D79)."""
    user = _current_user(request)
    day = _day_or_today(request.GET.get("day"))
    result = cash_reconciliation(user, day)
    return render(
        request,
        "staff/cash.html",
        {
            "day": day,
            "result": result,
            "collectors": result.collectors,
            "grand_total_text": money(result.grand_total),
            "unpaid_orders": result.unpaid_orders,
            **_nav_for(user),
        },
    )
