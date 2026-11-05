"""Cart views: a page, add, update and remove (D61).

Every mutation is POST only and CSRF protected. htmx gets a small fragment
with HTTP 200 even for a refused change, because htmx does not swap 4xx or 5xx
answers. A plain browser POST gets a message and a redirect instead.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import User
from apps.accounts.services import list_addresses
from apps.cart.forms import CartQuantityForm, CartRemoveForm
from apps.cart.presentation import (
    INVALID_QUANTITY_INPUT,
    ITEM_UNAVAILABLE,
    describe_cart_error,
    format_quantity,
)
from apps.cart.rules import MAX_LOOSE_PER_LINE, MAX_PACKETS_PER_LINE, CartError
from apps.cart.services import add_item, build_cart, remove_item, set_item_quantity
from apps.cart.stores import get_store
from apps.catalog.models import ProductVariant


def _is_htmx(request: HttpRequest) -> bool:
    """The fixed rule: an htmx request sends exactly ``HX-Request: true``."""
    return request.headers.get("HX-Request") == "true"


def _allowed_next(request: HttpRequest, next_url: str | None) -> str | None:
    """Return next_url only when it points at this site (no open redirects)."""
    if not next_url:
        return None
    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return None


def _form_error(form: CartQuantityForm | CartRemoveForm) -> str:
    """A clean sentence for bad input, never a form traceback or a 500."""
    if "quantity" in form.errors:
        return str(INVALID_QUANTITY_INPUT)
    return str(ITEM_UNAVAILABLE)


def _cart_context(request: HttpRequest, error: str | None = None) -> dict[str, object]:
    """Everything the cart block needs, with totals read live by the service."""
    return {
        "cart": build_cart(get_store(request.user, request.session)),
        "error": error,
        # The caps live in one place; the template only prints them.
        "loose_cap": MAX_LOOSE_PER_LINE,
        "packet_cap": MAX_PACKETS_PER_LINE,
    }


def _cart_body_response(request: HttpRequest, error: str | None = None) -> HttpResponse:
    """The whole cart block plus the out-of-band header count.

    ``render_to_string`` is used twice so the response is two fragments side by
    side; htmx swaps the body into ``#cart-body`` and finds ``#cart-count`` by
    its ``hx-swap-oob`` attribute.
    """
    body = render_to_string(
        "cart/_cart_body.html",
        _cart_context(request, error),
        request=request,
    )
    header_count = render_to_string(
        "cart/_header_count.html",
        {"oob": True},
        request=request,
    )
    return HttpResponse(body + header_count)


def _cart_failure(request: HttpRequest, message: str) -> HttpResponse:
    """A htmx error is a 200 fragment; a plain POST is a message and redirect."""
    if _is_htmx(request):
        return _cart_body_response(request, error=message)
    messages.error(request, message)
    return redirect("cart_detail")


def _add_failure(request: HttpRequest, message: str, next_url: str) -> HttpResponse:
    """The add target is a small result region, not the whole cart block."""
    if _is_htmx(request):
        return render(request, "cart/_add_result.html", {"error": message})
    messages.error(request, message)
    return redirect(next_url)


def _default_delivery_pincode(request: HttpRequest) -> str:
    """The pin code of a customer's default address, or "" when there is none.

    ``list_addresses`` already puts the default first. A guest gets an empty
    box: nothing is checked automatically (D62).
    """
    user = request.user
    if not isinstance(user, User) or not user.is_authenticated:
        return ""
    addresses = list_addresses(user)
    return addresses[0].pincode if addresses else ""


@require_GET
@never_cache
def cart_detail(request: HttpRequest) -> HttpResponse:
    """The cart page for a guest or a customer."""
    context = _cart_context(request)
    context["delivery_pincode"] = _default_delivery_pincode(request)
    return render(request, "cart/cart_detail.html", context)


@require_POST
@never_cache
def cart_add(request: HttpRequest) -> HttpResponse:
    """Add a quantity and stay on the page when htmx is available."""
    next_url = _allowed_next(request, request.POST.get("next")) or reverse("cart_detail")
    form = CartQuantityForm(request.POST)
    if not form.is_valid():
        return _add_failure(request, _form_error(form), next_url)

    # The add rules live in the service: an archived variant is found here and
    # refused there, so an unknown id and a hidden variant both stay clean.
    variant = ProductVariant.objects.filter(pk=form.cleaned_data["variant_id"]).first()
    if variant is None:
        return _add_failure(request, str(ITEM_UNAVAILABLE), next_url)

    quantity = form.cleaned_data["quantity"]
    try:
        add_item(get_store(request.user, request.session), variant, quantity)
    except CartError as error:
        return _add_failure(request, str(describe_cart_error(error, variant)), next_url)

    added = _("Added %(amount)s.") % {"amount": format_quantity(variant, quantity)}
    if _is_htmx(request):
        return render(request, "cart/_add_result.html", {"message": added})
    messages.success(request, added)
    return redirect(next_url)


@require_POST
@never_cache
def cart_update(request: HttpRequest) -> HttpResponse:
    """Replace a line's quantity; zero removes it (D60)."""
    form = CartQuantityForm(request.POST)
    if not form.is_valid():
        return _cart_failure(request, _form_error(form))

    # Deliberately no is_active filter: a customer must always be able to
    # change or remove a line whose variant was archived after it was added.
    variant = ProductVariant.objects.filter(pk=form.cleaned_data["variant_id"]).first()
    if variant is None:
        return _cart_failure(request, str(ITEM_UNAVAILABLE))

    try:
        set_item_quantity(
            get_store(request.user, request.session),
            variant,
            form.cleaned_data["quantity"],
        )
    except CartError as error:
        return _cart_failure(request, str(describe_cart_error(error, variant)))

    if _is_htmx(request):
        return _cart_body_response(request)
    return redirect("cart_detail")


@require_POST
@never_cache
def cart_remove(request: HttpRequest) -> HttpResponse:
    """Remove a line. Removing a line that is not there is a no-op."""
    next_url = _allowed_next(request, request.POST.get("next")) or reverse("cart_detail")
    form = CartRemoveForm(request.POST)
    if not form.is_valid():
        return _cart_failure(request, _form_error(form))

    variant = ProductVariant.objects.filter(pk=form.cleaned_data["variant_id"]).first()
    if variant is None:
        return _cart_failure(request, str(ITEM_UNAVAILABLE))

    remove_item(get_store(request.user, request.session), variant)

    if _is_htmx(request):
        return _cart_body_response(request)
    return redirect(next_url)
