"""Address book pages: list, add, edit, archive and set default (D65).

The pages are thin: the rules (limit, default, archive, normalisation) stay in
``apps.accounts.services`` and the wording stays in
``apps.accounts.address_presentation``.
"""

from __future__ import annotations

from typing import cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.accounts.address_presentation import (
    ARCHIVED_MESSAGE,
    DEFAULT_MESSAGE,
    SAVED_MESSAGE,
    UPDATED_MESSAGE,
    page_text,
    unserved_notice,
)
from apps.accounts.forms import AddressForm
from apps.accounts.models import Address, User
from apps.accounts.services import (
    MAX_ACTIVE_ADDRESSES,
    AddressLimitReached,
    AddressNotFound,
    archive_address,
    create_address,
    get_address,
    list_addresses,
    set_default_address,
    update_address,
)
from apps.core.redirects import safe_next_url
from apps.delivery.services import address_is_served

LOGIN_URL = reverse_lazy("account_login")


def _current_user(request: HttpRequest) -> User:
    # login_required has already refused a guest; the cast only tells mypy what
    # the decorator guarantees at run time.
    return cast(User, request.user)


def _address_or_404(user: User, pk: int) -> Address:
    """One active address of this user; a foreign or archived row is a 404."""
    try:
        address = get_address(user, pk)
    except AddressNotFound:
        raise Http404("Address not found.") from None
    if not address.is_active:
        raise Http404("Address not found.")
    return address


def _limit_page(request: HttpRequest, next_url: str | None) -> HttpResponse:
    """The add page at the limit: a message instead of the form (D65)."""
    return render(
        request,
        "accounts/address_form.html",
        {
            "text": page_text(),
            "limit_reached": True,
            "next_url": next_url,
        },
    )


@login_required(login_url=LOGIN_URL)
@never_cache
def address_list(request: HttpRequest) -> HttpResponse:
    """The user's active addresses, default first, with an unserved badge."""
    user = _current_user(request)
    addresses = list_addresses(user)
    # One small dict per card lets the template ask "is this one served?"
    # without calling the delivery service itself.
    cards = [{"address": address, "is_served": address_is_served(address)} for address in addresses]
    return render(
        request,
        "accounts/address_list.html",
        {
            "text": page_text(),
            "cards": cards,
            "at_limit": len(addresses) >= MAX_ACTIVE_ADDRESSES,
        },
    )


@login_required(login_url=LOGIN_URL)
@never_cache
def address_add(request: HttpRequest) -> HttpResponse:
    """Show the empty form, or save one address and go back to the list."""
    user = _current_user(request)
    next_url = safe_next_url(request, request.POST.get("next") or request.GET.get("next"))
    if len(list_addresses(user)) >= MAX_ACTIVE_ADDRESSES:
        return _limit_page(request, next_url)

    if request.method == "POST":
        form = AddressForm(request.POST)
        if form.is_valid():
            # **data passes the cleaned form values as keyword arguments; the
            # checkbox is not a model field, so it is removed first.
            data = dict(form.cleaned_data)
            make_default = data.pop("make_default")
            try:
                address = create_address(user, **data)
            except AddressLimitReached:
                # Two tabs could both pass the check above; the service is the
                # real gate, and it writes nothing when it refuses.
                return _limit_page(request, next_url)
            if make_default and not address.is_default:
                set_default_address(user, address.pk)
            messages.success(request, SAVED_MESSAGE)
            if not address_is_served(address):
                messages.warning(request, unserved_notice(address.pincode))
            return redirect(next_url or "address_list")
    else:
        form = AddressForm(initial={"recipient_phone": user.phone})

    return render(
        request,
        "accounts/address_form.html",
        {
            "text": page_text(),
            "form": form,
            "next_url": next_url,
            "limit_reached": False,
            "is_edit": False,
        },
    )


@login_required(login_url=LOGIN_URL)
@never_cache
def address_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Edit one active address of this user."""
    user = _current_user(request)
    address = _address_or_404(user, pk)
    next_url = safe_next_url(request, request.POST.get("next") or request.GET.get("next"))

    if request.method == "POST":
        form = AddressForm(request.POST)
        if form.is_valid():
            data = dict(form.cleaned_data)
            make_default = data.pop("make_default")
            updated = update_address(user, pk, **data)
            if make_default and not updated.is_default:
                set_default_address(user, updated.pk)
            messages.success(request, UPDATED_MESSAGE)
            if not address_is_served(updated):
                messages.warning(request, unserved_notice(updated.pincode))
            return redirect(next_url or "address_list")
    else:
        form = AddressForm(
            initial={
                "recipient_name": address.recipient_name,
                "recipient_phone": address.recipient_phone,
                "line1": address.line1,
                "line2": address.line2,
                "landmark": address.landmark,
                "city": address.city,
                "state": address.state,
                "pincode": address.pincode,
                "label": address.label,
            }
        )

    return render(
        request,
        "accounts/address_form.html",
        {
            "text": page_text(),
            "form": form,
            "address": address,
            "next_url": next_url,
            "limit_reached": False,
            "is_edit": True,
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def address_archive(request: HttpRequest, pk: int) -> HttpResponse:
    """Archive one active address and return to the list."""
    user = _current_user(request)
    _address_or_404(user, pk)
    archive_address(user, pk)
    messages.success(request, ARCHIVED_MESSAGE)
    return redirect("address_list")


@login_required(login_url=LOGIN_URL)
@require_POST
def address_set_default(request: HttpRequest, pk: int) -> HttpResponse:
    """Make one active address the default and return to the list."""
    user = _current_user(request)
    _address_or_404(user, pk)
    set_default_address(user, pk)
    messages.success(request, DEFAULT_MESSAGE)
    return redirect("address_list")
