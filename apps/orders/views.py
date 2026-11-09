"""Checkout and confirmation views (D66).

The double-submit guard is the important part: every step is its own small
private function so the order in ``_checkout_post`` can be reviewed in one
place. The rules themselves stay in ``apps.orders.services``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast
from urllib.parse import quote
from uuid import UUID, uuid4

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts.models import Address, User
from apps.accounts.services import list_addresses
from apps.accounts.verification_presentation import BANNER_HEADING, pending_verifications
from apps.cart.services import CartSummary, build_cart
from apps.cart.stores import DbCartStore
from apps.core.services import get_shop_settings
from apps.credit.services import available_credit
from apps.delivery.services import address_is_served, get_delivery_promise
from apps.inventory.reservations import ReservationShortfallError
from apps.orders.forms import CancelOrderForm, CheckoutForm
from apps.orders.models import Order
from apps.orders.presentation import (
    ADDRESS_NOT_FOUND_MESSAGE,
    ADDRESS_NOT_SERVED_MESSAGE,
    ALREADY_CANCELLED_MESSAGE,
    CANCEL_NOT_POSSIBLE_MESSAGE,
    CART_CHANGED_MESSAGE,
    CART_NOT_READY_MESSAGE,
    CREDIT_UNAVAILABLE_MESSAGE,
    EMPTY_CART_MESSAGE,
    GROUP_INVALID_MESSAGE,
    GROUP_REFUSED_MESSAGE,
    NO_ADDRESS_MESSAGE,
    ORDER_CANCELLED_MESSAGE,
    available_credit_sentence,
    cancel_question,
    cart_quantity,
    cod_limit_message,
    cod_on_delivery_sentence,
    cod_sentence,
    credit_choice_sentence,
    credit_confirmation_sentence,
    credit_limit_message,
    credit_refused_message,
    credit_returned_sentence,
    delivery_day_text,
    item_count_text,
    item_quantity,
    money,
    no_credit_left_message,
    order_date_text,
    page_label,
    page_text,
    payment_method_label,
    status_sentence,
    substitution_label,
    timeline_label,
    timeline_time_text,
    whatsapp_href,
)
from apps.orders.services import (
    CUSTOMER_CANCELLABLE,
    ORDER_LIST_PAGE_SIZE,
    AddressNotServedError,
    CartNotReadyError,
    CheckoutGroupInUseError,
    CodLimitExceededError,
    EmptyCartError,
    InvalidTransitionError,
    OrderAddressNotFoundError,
    OrderCreditLimitExceededError,
    OrderCreditUnavailableError,
    OrderNotFoundError,
    cancel_order_by_customer,
    get_user_order_by_group,
    list_user_order_rows,
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


def _posted_use_credit(request: HttpRequest) -> bool:
    """Whether the khata checkbox was ticked on the posted form."""
    return request.POST.get("use_credit") == "on"


def _payment_state(user: User) -> dict[str, object]:
    """The one fact the khata checkbox needs: is there any credit left (D74)."""
    # available_credit is zero without an account or with a suspended one.
    available = available_credit(user)
    return {
        "credit_offered": available > Decimal("0.00"),
        "available": available,
    }


def _render_checkout(
    request: HttpRequest,
    user: User,
    *,
    form: CheckoutForm,
    selected_address: str,
    selected_substitution: str,
    use_credit: bool = False,
    error: str | None = None,
) -> HttpResponse:
    """Render the checkout page with a fresh cart, address list and promise."""
    summary = _cart_for(user)
    rows = _address_rows(user, selected_address)
    has_served_address = any(bool(row["is_served"]) for row in rows)
    cod_cap = get_shop_settings().cod_cap
    payment = _payment_state(user)
    credit_offered = bool(payment["credit_offered"])
    available = cast(Decimal, payment["available"])
    # Without any credit left the checkbox is never shown, whatever was posted.
    if not credit_offered:
        use_credit = False
    # The customer cannot type an amount: the credit part is as much of the
    # order as their available credit covers, and the rest is cash (D74).
    credit_applied = min(available, summary.grand_total) if use_credit else Decimal("0.00")
    cash_due = summary.grand_total - credit_applied
    at_cod_limit = cash_due > cod_cap
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
            "use_credit": use_credit,
            "credit_applied": credit_applied,
            "cash_due": cash_due,
            "show_credit_row": use_credit,
            "show_cash_row": cash_due > Decimal("0.00"),
            "credit_sentence": (
                credit_choice_sentence(credit_applied, cash_due) if use_credit else ""
            ),
            "credit_limit_message": credit_limit_message(available),
            "can_submit": has_served_address and not at_cod_limit,
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
        # Ticked by default whenever the customer has credit to use (D74).
        use_credit=True,
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
            use_credit=_posted_use_credit(request),
        )

    # (e) The service is the only writer; it holds the stock and the order.
    payment_method = (
        Order.PaymentMethod.CREDIT if form.cleaned_data["use_credit"] else Order.PaymentMethod.COD
    )
    try:
        placed = place_order(
            user=user,
            address_id=form.cleaned_data["address"],
            payment_method=payment_method,
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
            use_credit=_posted_use_credit(request),
            error=str(ADDRESS_NOT_SERVED_MESSAGE),
        )
    except OrderAddressNotFoundError:
        return _render_checkout(
            request,
            user,
            form=form,
            selected_address=request.POST.get("address", ""),
            selected_substitution=_posted_substitution(request),
            use_credit=_posted_use_credit(request),
            error=str(ADDRESS_NOT_FOUND_MESSAGE),
        )
    except CodLimitExceededError:
        return _render_checkout(
            request,
            user,
            form=form,
            selected_address=request.POST.get("address", ""),
            selected_substitution=_posted_substitution(request),
            use_credit=_posted_use_credit(request),
            error=cod_limit_message(get_shop_settings().cod_cap),
        )
    except OrderCreditUnavailableError:
        return _render_checkout(
            request,
            user,
            form=form,
            selected_address=request.POST.get("address", ""),
            selected_substitution=_posted_substitution(request),
            use_credit=True,
            error=str(CREDIT_UNAVAILABLE_MESSAGE),
        )
    except OrderCreditLimitExceededError:
        available = available_credit(user)
        return _render_checkout(
            request,
            user,
            form=form,
            selected_address=request.POST.get("address", ""),
            selected_substitution=_posted_substitution(request),
            use_credit=True,
            error=(
                no_credit_left_message()
                if available <= Decimal("0.00")
                else credit_refused_message(available)
            ),
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


def _order_page_context(order: Order) -> dict[str, object]:
    """The values the confirmation and detail pages share (D66, D70).

    Keeping one helper means the two pages cannot drift: the confirmation page
    adds its own title text and the detail page adds its timeline and actions.
    """
    # The same line shape the checkout page uses, from the frozen item copies.
    lines = [
        {
            "name": item.product_name,
            "quantity_text": item_quantity(item),
            "line_total": item.line_total,
        }
        for item in order.items.all()
    ]
    is_credit = order.credit_applied > Decimal("0.00")
    cash_due = order.amount_due_on_delivery
    return {
        "order": order,
        "summary": order,
        "lines": lines,
        "status_sentence": status_sentence(order.status),
        "delivery_day": delivery_day_text(order.promised_delivery_date),
        "substitution_choice": substitution_label(order.substitution_preference),
        "cod_sentence": cod_on_delivery_sentence(cash_due) if cash_due > Decimal("0.00") else "",
        "is_credit": is_credit,
        "credit_applied": order.credit_applied,
        "cash_due": cash_due,
        "show_credit_row": is_credit,
        "show_cash_row": cash_due > Decimal("0.00"),
        "payment_method": payment_method_label(order.payment_method),
        "credit_sentence": (
            credit_confirmation_sentence(order.credit_applied) if is_credit else ""
        ),
    }


@login_required(login_url=LOGIN_URL)
@never_cache
def order_confirmation(request: HttpRequest, group: UUID) -> HttpResponse:
    """The customer's own confirmation page, found by checkout group (D66)."""
    user = _current_user(request)
    try:
        order = get_user_order_by_group(user, group)
    except OrderNotFoundError:
        raise Http404("Order not found.") from None

    # The ** spreads the shared keys into this page's own context dict.
    return render(
        request,
        "orders/order_confirmation.html",
        {
            "text": page_text(),
            **_order_page_context(order),
            "detail_url": reverse("order_detail", args=[order.checkout_group]),
            # The banner lists only what the customer still has to verify (D71).
            "verification_items": pending_verifications(user),
            "verification_heading": str(BANNER_HEADING),
        },
    )


@login_required(login_url=LOGIN_URL)
@require_http_methods(["GET"])
@never_cache
def order_detail(request: HttpRequest, group: UUID) -> HttpResponse:
    """One order with its progress timeline and actions (D70)."""
    user = _current_user(request)
    try:
        order = get_user_order_by_group(user, group)
    except OrderNotFoundError:
        raise Http404("Order not found.") from None

    # The prefetched logs arrive oldest first (the model's own order), and the
    # timeline never shows the log note, the actor or the internal reason.
    timeline = [
        {
            "label": timeline_label(log.to_status),
            "time_text": timeline_time_text(log.created_at),
        }
        for log in order.status_logs.all()
    ]
    cancelled_credit = (
        order.status == Order.Status.CANCELLED
        and order.payment_method == Order.PaymentMethod.CREDIT
    )
    # The ** spreads the shared keys into this page's own context dict.
    return render(
        request,
        "orders/order_detail.html",
        {
            "text": page_text(),
            **_order_page_context(order),
            "timeline": timeline,
            "placed_on": order_date_text(order.created_at),
            "can_cancel": order.status in CUSTOMER_CANCELLABLE,
            "cancel_url": reverse("order_cancel", args=[order.checkout_group]),
            "whatsapp_href": whatsapp_href(get_shop_settings().whatsapp_number, order.order_number),
            "credit_returned_text": (
                credit_returned_sentence(order.credit_applied) if cancelled_credit else ""
            ),
            "orders_url": reverse("order_list"),
            "verification_items": pending_verifications(user),
            "verification_heading": str(BANNER_HEADING),
        },
    )


def _render_cancel(request: HttpRequest, order: Order, form: CancelOrderForm) -> HttpResponse:
    """Show the cancel page, with a form error when the reason was too long."""
    return render(
        request,
        "orders/order_cancel.html",
        {
            "text": page_text(),
            "order": order,
            "form": form,
            "cancel_question": cancel_question(order.order_number),
            "cancel_url": reverse("order_cancel", args=[order.checkout_group]),
            "detail_url": reverse("order_detail", args=[order.checkout_group]),
        },
    )


def _redirect_already_cancelled(request: HttpRequest, order: Order) -> HttpResponse:
    """Send an already-cancelled order back to its detail page."""
    messages.info(request, ALREADY_CANCELLED_MESSAGE)
    return redirect("order_detail", group=order.checkout_group)


def _redirect_cannot_cancel(request: HttpRequest, order: Order) -> HttpResponse:
    """Send an order that is past packing back to its detail page."""
    messages.error(request, CANCEL_NOT_POSSIBLE_MESSAGE)
    return redirect("order_detail", group=order.checkout_group)


@login_required(login_url=LOGIN_URL)
@require_http_methods(["GET", "POST"])
@never_cache
def order_cancel(request: HttpRequest, group: UUID) -> HttpResponse:
    """Ask before cancelling, then call the existing cancel service (D70)."""
    user = _current_user(request)
    try:
        order = get_user_order_by_group(user, group)
    except OrderNotFoundError:
        raise Http404("Order not found.") from None

    # These two checks come before the form, so a second POST after a success
    # hits the already-cancelled branch and can never reverse a credit twice.
    if order.status == Order.Status.CANCELLED:
        return _redirect_already_cancelled(request, order)
    if order.status not in CUSTOMER_CANCELLABLE:
        return _redirect_cannot_cancel(request, order)

    if request.method == "GET":
        return _render_cancel(request, order, CancelOrderForm())

    form = CancelOrderForm(request.POST)
    if not form.is_valid():
        return _render_cancel(request, order, form)

    try:
        cancel_order_by_customer(
            user,
            order.pk,
            reason=form.cleaned_data["reason"],
        )
    except OrderNotFoundError:
        raise Http404("Order not found.") from None
    except InvalidTransitionError:
        # Staff may have moved the order between the checks above and the lock.
        return _redirect_cannot_cancel(request, order)

    messages.success(request, ORDER_CANCELLED_MESSAGE)
    return redirect("order_detail", group=order.checkout_group)


@login_required(login_url=LOGIN_URL)
@require_http_methods(["GET"])
@never_cache
def order_list(request: HttpRequest) -> HttpResponse:
    """The customer's own orders, newest first, ten per page (D70)."""
    user = _current_user(request)
    # get_page turns a bad or too-large page number into a valid page by itself.
    page_obj = Paginator(list_user_order_rows(user), ORDER_LIST_PAGE_SIZE).get_page(
        request.GET.get("page")
    )
    rows = [
        {
            "order": order,
            "status_text": status_sentence(order.status),
            "total_text": money(order.grand_total),
            "payment_text": payment_method_label(order.payment_method),
            # The item count is an annotation, which mypy cannot see on the model.
            "items_text": item_count_text(cast(Any, order).item_count),
            "date_text": order_date_text(order.created_at),
            "url": reverse("order_detail", args=[order.checkout_group]),
        }
        for order in page_obj
    ]
    return render(
        request,
        "orders/order_list.html",
        {
            "text": page_text(),
            "rows": rows,
            "page_obj": page_obj,
            "page_text_label": (
                page_label(page_obj.number, page_obj.paginator.num_pages)
                if page_obj.paginator.num_pages > 1
                else ""
            ),
        },
    )
