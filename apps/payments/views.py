"""Customer payment pages and the provider webhook (D77).

The provider's webhook or a server-to-server fetch are the only things that can
mark money as received. The browser's redirect proves nothing, so the return
page asks the provider itself, and the status page only reads the database.
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
    refresh_payment_from_gateway,
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
    """Ask the provider about the payment, then show the stored facts (D77)."""
    order = _own_order(request, order_number)
    # This GET changes state on purpose. It is safe because the service is
    # idempotent and the provider, not the browser, supplies the facts.
    refresh_payment_from_gateway(_current_user(request), order_number)
    order.refresh_from_db()
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
def payment_webhook(request: HttpRequest, provider: str) -> JsonResponse:
    """Verify and apply one provider webhook; retries are safe (D77).

    The route carries the provider name, so no provider or header name appears
    in this module. A wrong name is a 404, exactly like an unknown page.
    """
    try:
        gateway = get_gateway()
    except PaymentGatewayError:
        gateway = None
    if gateway is None or provider != gateway.name:
        raise Http404("Payment provider not found.")
    try:
        event = gateway.parse_webhook(request.body, request.headers)
    except PaymentGatewayError:
        # Never echo the body or the signature: they are untrusted data.
        return JsonResponse({"detail": "Invalid webhook."}, status=400)
    if event is None:
        return JsonResponse({"status": "ignored"})
    handle_gateway_event(event, provider=gateway.name)
    return JsonResponse({"status": "ok"})
