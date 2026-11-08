"""Phone and email verification pages (D71).

Each page shows the masked contact and then one of three states: already
verified, no email on the account, or the two small forms (ask for a code, and
enter it). The rules stay in ``apps.accounts.verification`` and the wording in
``apps.accounts.verification_presentation``.
"""

from __future__ import annotations

from typing import cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts.forms import VerificationCodeForm
from apps.accounts.models import User
from apps.accounts.verification import (
    confirm_email_code,
    confirm_phone_code,
    request_email_code,
    request_phone_code,
)
from apps.accounts.verification_presentation import (
    CODE_ERROR_MESSAGE,
    CODE_SENT_MESSAGE,
    EMAIL_HEADING,
    EMAIL_TITLE,
    PHONE_HEADING,
    PHONE_TITLE,
    WAIT_MESSAGE,
    mask_email,
    mask_phone,
    verification_text,
)

LOGIN_URL = reverse_lazy("account_login")

# The one POST field that says which small form was sent.
ACTION_FIELD = "action"
SEND_ACTION = "send"
CONFIRM_ACTION = "confirm"


def _verification_page(request: HttpRequest, *, is_email: bool) -> HttpResponse:
    """The shared page for the phone and the email (D71).

    A wrong, expired or used code always gives the same one message, so the
    page cannot say which check failed.
    """
    # login_required has already refused a guest; the cast only tells mypy.
    user = cast(User, request.user)
    page_name = "account_verify_email" if is_email else "account_verify_phone"
    page_url = reverse(page_name)

    # A customer with no email is explained to and never asked for one.
    no_email = is_email and not user.email
    if is_email:
        verified = user.email_verified_at is not None
    else:
        verified = user.phone_verified_at is not None
    if no_email:
        masked_contact = ""
    elif is_email:
        masked_contact = mask_email(user.email or "")
    else:
        masked_contact = mask_phone(user.phone)

    form = VerificationCodeForm()
    # A verified page and the no-email page have no form, so a posted action
    # there changes nothing and the page is shown again.
    if request.method == "POST" and not verified and not no_email:
        action = request.POST.get(ACTION_FIELD)
        if action == SEND_ACTION:
            result = request_email_code(user) if is_email else request_phone_code(user)
            if result.sent:
                messages.success(request, CODE_SENT_MESSAGE)
            else:
                # "Too soon" and "too many requests" share one wait message.
                messages.error(request, WAIT_MESSAGE)
            return redirect(page_name)
        if action == CONFIRM_ACTION:
            form = VerificationCodeForm(request.POST)
            if form.is_valid():
                confirmed = (
                    confirm_email_code(user, form.cleaned_data["code"])
                    if is_email
                    else confirm_phone_code(user, form.cleaned_data["code"])
                )
                if confirmed:
                    # Redirect, so a refresh cannot confirm the same code twice.
                    return redirect(page_name)
                messages.error(request, CODE_ERROR_MESSAGE)

    return render(
        request,
        "accounts/verify_contact.html",
        {
            "text": verification_text(),
            "title": str(EMAIL_TITLE if is_email else PHONE_TITLE),
            "heading": str(EMAIL_HEADING if is_email else PHONE_HEADING),
            "masked_contact": masked_contact,
            "verified": verified,
            "no_email": no_email,
            "form": form,
            "page_url": page_url,
        },
    )


@login_required(login_url=LOGIN_URL)
@never_cache
@require_http_methods(["GET", "POST"])
def verify_phone(request: HttpRequest) -> HttpResponse:
    """Verify the account's mobile number with an SMS code."""
    return _verification_page(request, is_email=False)


@login_required(login_url=LOGIN_URL)
@never_cache
@require_http_methods(["GET", "POST"])
def verify_email(request: HttpRequest) -> HttpResponse:
    """Verify the account's email address with an email code."""
    return _verification_page(request, is_email=True)
