"""Customer payment pages and the provider webhook (D77).

The webhook is the only writer of a captured payment. The return and status
pages read the database, so a forged query string cannot mark an order paid.
"""

from __future__ import annotations

from typing import cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import User
from apps.orders.models import Order
from apps.orders.presentation import page_text, status_sentence
from apps.orders.services import OrderNotFoundError, get_user_order_by_number
from apps.payments.gateway import (
    PAYMENT_CAPTURED,
    PAYMENT_FAILED,
    GatewayNotConfigured,
    PaymentGatewayError,
    get_gateway,
)
from apps.payments.models import Payment
from apps.payments.services import (
    PaymentAlreadyCapturedError,
    PaymentNotAllowedError,
    handle_gateway_event,
    latest_payment,
    start_online_payment,
)

LOGIN_URL = reverse_lazy("account_login")


def _current_user(request: HttpRequest) -> User:
    # login_required has already refused a guest; the cast only tells mypy.
    return cast(User, request.user)


def _own_order(request: HttpRequest, order_number: str) -> Order:
    try:
        return get_user_order_by_number(_current_user(request), order_number)
    except OrderNotFoundError:
        raise Http404("Order not found.") from None


def _status_values(order: Order, payment: Payment | None) -> dict[str, object]:
    """The few values the return and status pages share."""
    captured = payment is not None and payment.status == Payment.Status.CAPTURED
    failed = order.status == Order.Status.PAYMENT_FAILED or (
        payment is not None and payment.status == Payment.Status.FAILED
    )
    can_pay = (
        order.online_amount > 0
        and not captured
        and order.status
        in (
            Order.Status.PLACED,
            Order.Status.PAYMENT_PENDING,
            Order.Status.PAYMENT_FAILED,
        )
    )
    return {
        "order": order,
        "payment": payment,
        "captured": captured,
        "failed": failed,
        "can_pay": can_pay,
        "pay_url": reverse("payment_start", args=[order.order_number]),
        "cart_url": reverse("cart_detail"),
        "detail_url": reverse("order_detail", args=[order.checkout_group]),
        "status_sentence": status_sentence(order.status),
        "status_url": reverse("payment_status", args=[order.order_number]),
    }


@login_required(login_url=LOGIN_URL)
@require_POST
@never_cache
def payment_start(request: HttpRequest, order_number: str) -> HttpResponse:
    """Create the provider order and show the gateway page (D77)."""
    order = _own_order(request, order_number)
    try:
        started = start_online_payment(_current_user(request), order_number)
    except PaymentAlreadyCapturedError:
        messages.info(request, "This order is already paid online.")
        return redirect("order_detail", group=order.checkout_group)
    except (PaymentNotAllowedError, GatewayNotConfigured, PaymentGatewayError):
        messages.error(
            request,
            "Online payment is not available for this order right now. "
            "Please update your cart or call the shop.",
        )
        return redirect("order_detail", group=order.checkout_group)

    # The provider template is chosen by name, never by a user value.
    template = f"payments/gateways/{started.session.provider}.html"
    return render(
        request,
        "payments/pay.html",
        {
            "text": page_text(),
            "order": order,
            "session": started.session,
            "provider_template": template,
            # The provider sends the browser here after the payment attempt.
            "callback_url": reverse("payment_return", args=[order.order_number]),
        },
    )


@login_required(login_url=LOGIN_URL)
@require_GET
@never_cache
def payment_return(request: HttpRequest, order_number: str) -> HttpResponse:
    """The page the gateway redirects to; it only reads the database (D77)."""
    order = _own_order(request, order_number)
    payment = latest_payment(order)
    return render(
        request,
        "payments/payment_return.html",
        {
            "text": page_text(),
            **_status_values(order, payment),
            "pending": not (payment is not None and payment.status != Payment.Status.CREATED),
        },
    )


@login_required(login_url=LOGIN_URL)
@require_GET
@never_cache
def payment_status(request: HttpRequest, order_number: str) -> HttpResponse:
    """A small fragment the return page can poll (D77)."""
    order = _own_order(request, order_number)
    return render(
        request,
        "payments/_payment_status.html",
        {
            "text": page_text(),
            **_status_values(order, latest_payment(order)),
        },
    )


@csrf_exempt
@require_POST
def razorpay_webhook(request: HttpRequest) -> JsonResponse:
    """Verify and apply one provider webhook; retries are safe (D77)."""
    try:
        gateway = get_gateway()
        if gateway is None:
            return JsonResponse({"detail": "Payments are not configured."}, status=400)
        event = gateway.parse_webhook(
            request.body,
            request.headers.get("X-Razorpay-Signature", ""),
            event_id=request.headers.get("X-Razorpay-Event-Id", ""),
        )
        # Only a captured or failed event is understood; parse_webhook refuses
        # anything else before this line.
        if event.kind not in (PAYMENT_CAPTURED, PAYMENT_FAILED):
            return JsonResponse({"detail": "Event not handled."}, status=400)
        handle_gateway_event(event, provider=gateway.name)
    except PaymentGatewayError:
        # Never echo the body or the signature: they are untrusted data.
        return JsonResponse({"detail": "Invalid webhook."}, status=400)
    return JsonResponse({"status": "ok"})
