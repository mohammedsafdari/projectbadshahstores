"""Checkout and confirmation views (D66).

The double-submit guard is the important part: every step is its own small
private function so the order in ``_checkout_post`` can be reviewed in one
place. The rules themselves stay in ``apps.orders.services``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import cast
from urllib.parse import quote
from uuid import UUID, uuid4

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts.models import Address, User
from apps.accounts.services import list_addresses
from apps.cart.services import CartSummary, build_cart
from apps.cart.stores import DbCartStore
from apps.core.services import get_shop_settings
from apps.credit.models import CreditAccount
from apps.credit.services import available_credit
from apps.delivery.services import address_is_served, get_delivery_promise
from apps.inventory.reservations import ReservationShortfallError
from apps.orders.forms import CheckoutForm
from apps.orders.models import Order
from apps.orders.presentation import (
    ADDRESS_NOT_FOUND_MESSAGE,
    ADDRESS_NOT_SERVED_MESSAGE,
    CART_CHANGED_MESSAGE,
    CART_NOT_READY_MESSAGE,
    CREDIT_UNAVAILABLE_MESSAGE,
    EMPTY_CART_MESSAGE,
    GROUP_INVALID_MESSAGE,
    GROUP_REFUSED_MESSAGE,
    NO_ADDRESS_MESSAGE,
    available_credit_sentence,
    cart_quantity,
    cod_limit_message,
    cod_on_delivery_sentence,
    cod_sentence,
    credit_checkout_sentence,
    credit_confirmation_sentence,
    credit_limit_message,
    credit_refused_message,
    delivery_day_text,
    item_quantity,
    page_text,
    payment_method_label,
    status_sentence,
    substitution_label,
)
from apps.orders.services import (
    AddressNotServedError,
    CartNotReadyError,
    CheckoutGroupInUseError,
    CodLimitExceededError,
    EmptyCartError,
    OrderAddressNotFoundError,
    OrderCreditLimitExceededError,
    OrderCreditUnavailableError,
    OrderNotFoundError,
    get_user_order_by_group,
    place_order,
)

LOGIN_URL = reverse_lazy("account_login")


def _current_user(request: HttpRequest) -> User:
    # login_required has already refused a guest; the cast only tells mypy.
    return cast(User, request.user)


def _cart_for(user: User) -> CartSummary:
    """The customer's cart with live prices and totals."""
    return build_cart(DbCartStore(user))


def _address_rows(user: User, selected: str) -> list[dict[str, object]]:
    """One small dict per saved address, so the template only prints values."""
    rows: list[dict[str, object]] = []
    for address in list_addresses(user):
        rows.append(
            {
                "address": address,
                "is_served": address_is_served(address),
                "selected": str(address.pk) == selected,
            }
        )
    return rows


def _first_served_pk(rows: list[dict[str, object]]) -> str:
    """The address selected first: the default one is already first in the list."""
    for row in rows:
        if row["is_served"]:
            address = cast(Address, row["address"])
            return str(address.pk)
    return ""


def _posted_substitution(request: HttpRequest) -> str:
    """The posted choice when it is valid, otherwise the model default."""
    value = request.POST.get("substitution_preference", "")
    choices = {choice for choice, _label in Order.SubstitutionPreference.choices}
    return value if value in choices else Order.SubstitutionPreference.CALL_ME


def _session_group(request: HttpRequest) -> str:
    """The one checkout group for this session, created on the first GET."""
    group = request.session.get("checkout_group")
    if not isinstance(group, str) or not group:
        group = str(uuid4())
        request.session["checkout_group"] = group
    return group


def _posted_payment(request: HttpRequest) -> str:
    """The posted method when it is valid, otherwise an empty string."""
    value = request.POST.get("payment_method", "")
    choices = {choice for choice, _label in Order.PaymentMethod.choices}
    return value if value in choices else ""


def _payment_state(user: User, summary: CartSummary) -> dict[str, object]:
    """The four facts the payment radios need, read once (D68)."""
    # Credit is offered only to a customer with an active khata.
    credit_offered = CreditAccount.objects.filter(user=user, is_active=True).exists()
    # What the customer may still charge, zero without an active account.
    available = available_credit(user)
    # Cash on delivery is allowed unless the order is above the shop cap.
    cod_allowed = summary.grand_total <= get_shop_settings().cod_cap
    # Credit fits only when the offered account covers the whole total.
    credit_fits = credit_offered and summary.grand_total <= available
    return {
        "credit_offered": credit_offered,
        "available": available,
        "cod_allowed": cod_allowed,
        "credit_fits": credit_fits,
    }


def _render_checkout(
    request: HttpRequest,
    user: User,
    *,
    form: CheckoutForm,
    selected_address: str,
    selected_substitution: str,
    selected_payment: str = "",
    error: str | None = None,
) -> HttpResponse:
    """Render the checkout page with a fresh cart, address list and promise."""
    summary = _cart_for(user)
    rows = _address_rows(user, selected_address)
    has_served_address = any(bool(row["is_served"]) for row in rows)
    cod_cap = get_shop_settings().cod_cap
    at_cod_limit = summary.grand_total > cod_cap
    payment = _payment_state(user, summary)
    credit_offered = bool(payment["credit_offered"])
    available = cast(Decimal, payment["available"])
    cod_allowed = bool(payment["cod_allowed"])
    credit_fits = bool(payment["credit_fits"])
    # The posted method wins; otherwise COD first, then credit if COD is out.
    if selected_payment not in (Order.PaymentMethod.COD, Order.PaymentMethod.CREDIT):
        selected_payment = Order.PaymentMethod.COD if cod_allowed else Order.PaymentMethod.CREDIT
    # One small dict per line, so the shared summary partial prints values only.
    lines = [
        {
            "name": line.variant.product.name_en,
            "quantity_text": cart_quantity(line),
            "line_total": line.line_total,
        }
        for line in summary.lines
    ]
    return render(
        request,
        "orders/checkout.html",
        {
            "text": page_text(),
            "form": form,
            "cards": rows,
            "has_served_address": has_served_address,
            "cart": summary,
            "summary": summary,
            "lines": lines,
            "promise": get_delivery_promise(),
            "checkout_group": _session_group(request),
            "checkout_url": reverse("checkout"),
            "selected_substitution": selected_substitution,
            "substitution_choices": [
                (value, substitution_label(value))
                for value, _label in Order.SubstitutionPreference.choices
            ],
            "cod_sentence": cod_sentence(summary.grand_total),
            "at_cod_limit": at_cod_limit,
            "limit_message": cod_limit_message(cod_cap),
            "credit_offered": credit_offered,
            "available": available,
            "available_credit_text": available_credit_sentence(available),
            "cod_allowed": cod_allowed,
            "credit_fits": credit_fits,
            "selected_payment": selected_payment,
            "credit_sentence": credit_checkout_sentence(summary.grand_total),
            "credit_limit_message": credit_limit_message(available),
            "can_submit": has_served_address and (cod_allowed or credit_fits),
            "error": error,
        },
    )


def _checkout_get(request: HttpRequest, user: User) -> HttpResponse:
    """Refuse an unready cart, then show the form and create a group."""
    summary = _cart_for(user)
    if not summary.lines:
        messages.info(request, EMPTY_CART_MESSAGE)
        return redirect("cart_detail")
    if not summary.checkout_ready:
        messages.info(request, CART_NOT_READY_MESSAGE)
        return redirect("cart_detail")

    if not list_addresses(user):
        messages.info(request, NO_ADDRESS_MESSAGE)
        next_url = reverse("checkout")
        return redirect(f"{reverse('address_add')}?next={quote(next_url)}")

    rows = _address_rows(user, "")
    selected = _first_served_pk(rows)
    form = CheckoutForm(initial={"address": int(selected) if selected else None})
    return _render_checkout(
        request,
        user,
        form=form,
        selected_address=selected,
        selected_substitution=Order.SubstitutionPreference.CALL_ME,
    )


def _existing_order(user: User, group: UUID) -> Order | None:
    """Step (b): this customer's order for the group, when it already exists."""
    try:
        return get_user_order_by_group(user, group)
    except OrderNotFoundError:
        return None


def _posted_group(request: HttpRequest) -> UUID | None:
    """Step (a): the posted checkout group, or None when it is not a UUID."""
    try:
        return UUID(request.POST.get("checkout_group", ""))
    except (AttributeError, TypeError, ValueError):
        return None


def _session_group_matches(request: HttpRequest, group: UUID) -> bool:
    """Step (c): whether the posted group is the one this session was given."""
    return request.session.get("checkout_group") == str(group)


def _refuse_checkout(request: HttpRequest) -> HttpResponse:
    """Refuse a stale group, replace it and send the customer back to checkout."""
    request.session["checkout_group"] = str(uuid4())
    messages.error(request, GROUP_REFUSED_MESSAGE)
    return redirect("checkout")


def _checkout_post(request: HttpRequest, user: User) -> HttpResponse:
    """Place one COD or credit order, with the double-submit steps in fixed order."""
    # (a) A posted group that is not a UUID is refused before anything else.
    group = _posted_group(request)
    if group is None:
        messages.error(request, GROUP_INVALID_MESSAGE)
        return redirect("checkout")

    # (b) An existing order of this customer wins: a double click, a refresh
    # or a Back-then-submit returns to the same confirmation page.
    existing = _existing_order(user, group)
    if existing is not None:
        return redirect("order_confirmation", group=existing.checkout_group)

    # (c) Anything else must be the group this session received on GET.
    if not _session_group_matches(request, group):
        return _refuse_checkout(request)

    # (d) Validate the form only after the group checks.
    form = CheckoutForm(request.POST)
    if not form.is_valid():
        return _render_checkout(
            request,
            user,
            form=form,
            selected_address=request.POST.get("address", ""),
            selected_substitution=_posted_substitution(request),
            selected_payment=_posted_payment(request),
        )

    # (e) The service is the only writer; it holds the stock and the order.
    try:
        placed = place_order(
            user=user,
            address_id=form.cleaned_data["address"],
            payment_method=form.cleaned_data["payment_method"],
            checkout_group=group,
            substitution_preference=form.cleaned_data["substitution_preference"],
            customer_note=form.cleaned_data["customer_note"],
            now=timezone.now(),
        )
    except (EmptyCartError, CartNotReadyError, ReservationShortfallError):
        messages.info(request, CART_CHANGED_MESSAGE)
        return redirect("cart_detail")
    except AddressNotServedError:
        return _render_checkout(
            request,
            user,
            form=form,
            selected_address=request.POST.get("address", ""),
            selected_substitution=_posted_substitution(request),
            selected_payment=_posted_payment(request),
            error=str(ADDRESS_NOT_SERVED_MESSAGE),
        )
    except OrderAddressNotFoundError:
        return _render_checkout(
            request,
            user,
            form=form,
            selected_address=request.POST.get("address", ""),
            selected_substitution=_posted_substitution(request),
            selected_payment=_posted_payment(request),
            error=str(ADDRESS_NOT_FOUND_MESSAGE),
        )
    except CodLimitExceededError:
        return _render_checkout(
            request,
            user,
            form=form,
            selected_address=request.POST.get("address", ""),
            selected_substitution=_posted_substitution(request),
            selected_payment=Order.PaymentMethod.COD,
            error=cod_limit_message(get_shop_settings().cod_cap),
        )
    except OrderCreditUnavailableError:
        return _render_checkout(
            request,
            user,
            form=form,
            selected_address=request.POST.get("address", ""),
            selected_substitution=_posted_substitution(request),
            selected_payment=Order.PaymentMethod.CREDIT,
            error=str(CREDIT_UNAVAILABLE_MESSAGE),
        )
    except OrderCreditLimitExceededError:
        return _render_checkout(
            request,
            user,
            form=form,
            selected_address=request.POST.get("address", ""),
            selected_substitution=_posted_substitution(request),
            selected_payment=Order.PaymentMethod.CREDIT,
            error=credit_refused_message(available_credit(user)),
        )
    except CheckoutGroupInUseError:
        return _refuse_checkout(request)

    # (f) Success: the session group is spent, so the next checkout gets a new one.
    request.session.pop("checkout_group", None)
    return redirect("order_confirmation", group=placed.order.checkout_group)


@login_required(login_url=LOGIN_URL)
@require_http_methods(["GET", "POST"])
@never_cache
def checkout(request: HttpRequest) -> HttpResponse:
    """Show the checkout page, or place one COD or credit order (D66, D68)."""
    user = _current_user(request)
    if request.method == "POST":
        return _checkout_post(request, user)
    return _checkout_get(request, user)


@login_required(login_url=LOGIN_URL)
@never_cache
def order_confirmation(request: HttpRequest, group: UUID) -> HttpResponse:
    """The customer's own confirmation page, found by checkout group (D66)."""
    user = _current_user(request)
    try:
        order = get_user_order_by_group(user, group)
    except OrderNotFoundError:
        raise Http404("Order not found.") from None

    # The same line shape the checkout page uses, from the frozen item copies.
    lines = [
        {
            "name": item.product_name,
            "quantity_text": item_quantity(item),
            "line_total": item.line_total,
        }
        for item in order.items.all()
    ]
    is_credit = order.payment_method == Order.PaymentMethod.CREDIT
    return render(
        request,
        "orders/order_confirmation.html",
        {
            "text": page_text(),
            "order": order,
            "summary": order,
            "lines": lines,
            "status_sentence": status_sentence(order.status),
            "delivery_day": delivery_day_text(order.promised_delivery_date),
            "substitution_choice": substitution_label(order.substitution_preference),
            "cod_sentence": cod_on_delivery_sentence(order.amount_due_on_delivery),
            "is_credit": is_credit,
            "payment_method": payment_method_label(order.payment_method),
            "credit_sentence": (
                credit_confirmation_sentence(order.grand_total) if is_credit else ""
            ),
        },
    )
