"""Forgot-password, reset and the logged-in account pages (D73).

The pages stay thin: the forms shape the input, ``password_reset`` holds the
reset rules and ``passwords`` the lock. Every page is ``noindex`` (the
templates) and never cached.
"""

from __future__ import annotations

from typing import cast

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts.auth_presentation import (
    ACCOUNT_NAME_FALLBACK,
    ADD_EMAIL_LINK,
    CHANGE_EMAIL_LINK,
    CHANGE_PASSWORD_LINK,
    CURRENT_PASSWORD_WRONG_MESSAGE,
    EMAIL_PAGE_HEADING_ADD,
    EMAIL_PAGE_HEADING_CHANGE,
    EMAIL_REMOVED_MESSAGE,
    EMAIL_SAVED_MESSAGE,
    EMAIL_UNCHANGED_MESSAGE,
    EMAIL_UNUSABLE_MESSAGE,
    FORGOT_SENT_MESSAGE,
    LOGIN_LOCKED_MESSAGE,
    PASSWORD_CHANGED_MESSAGE,
    PASSWORD_PAGE_HEADING_CHANGE,
    PASSWORD_PAGE_HEADING_SET,
    PASSWORD_SAVED_MESSAGE,
    RESET_CODE_ERROR_MESSAGE,
    RESET_NEED_REQUEST_MESSAGE,
    SET_PASSWORD_LINK,
    auth_text,
)
from apps.accounts.forms import (
    ChangeEmailForm,
    ChangePasswordForm,
    ForgotPasswordForm,
    ResetPasswordForm,
)
from apps.accounts.models import User
from apps.accounts.password_reset import (
    CHANNEL_EMAIL,
    CHANNEL_PHONE,
    SESSION_RESET_CHANNEL,
    SESSION_RESET_PHONE,
    confirm_password_reset,
    request_password_reset,
)
from apps.accounts.passwords import (
    clear_login_failures,
    find_customer,
    login_locked,
    record_login_failure,
)
from apps.accounts.verification import invalidate_email_codes
from apps.accounts.verification_presentation import (
    BANNER_EMAIL,
    BANNER_PHONE,
    mask_email,
    mask_phone,
)

LOGIN_URL = reverse_lazy("account_login")


@never_cache
@require_http_methods(["GET", "POST"])
def forgot_password(request: HttpRequest) -> HttpResponse:
    """Ask for a reset code by phone or email (D73).

    A known account, an unknown one and a rate-limited request all get the same
    answer: the same message and the same redirect.
    """
    form = ForgotPasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        result = request_password_reset(form.cleaned_data["identifier"])
        # Only these two values ever enter the session.
        request.session[SESSION_RESET_PHONE] = result.phone
        request.session[SESSION_RESET_CHANNEL] = result.channel
        messages.info(request, FORGOT_SENT_MESSAGE)
        return redirect("account_reset_password")
    return render(
        request,
        "accounts/forgot_password.html",
        {"form": form, "text": auth_text()},
    )


@never_cache
@require_http_methods(["GET", "POST"])
def reset_password(request: HttpRequest) -> HttpResponse:
    """Enter the code and the new password, then go back to the login (D73)."""
    phone = request.session.get(SESSION_RESET_PHONE)
    channel = request.session.get(SESSION_RESET_CHANNEL)
    # An empty phone is still a real request (an unknown email); only a request
    # that never happened has no keys at all.
    if phone is None or channel not in (CHANNEL_PHONE, CHANNEL_EMAIL):
        messages.info(request, RESET_NEED_REQUEST_MESSAGE)
        return redirect("account_forgot_password")

    # The found customer only feeds the similarity validator; the service
    # repeats the lookup and is the only writer.
    user = find_customer(phone) if phone else None
    form = ResetPasswordForm(request.POST or None, user=user)
    if request.method == "POST" and form.is_valid():
        outcome = confirm_password_reset(
            phone=phone,
            channel=channel,
            code=form.cleaned_data["code"],
            new_password=form.cleaned_data["new_password1"],
        )
        if not outcome.ok:
            # The same sentence for a wrong, expired, used or unknown code.
            messages.error(request, RESET_CODE_ERROR_MESSAGE)
        else:
            request.session.pop(SESSION_RESET_PHONE, None)
            request.session.pop(SESSION_RESET_CHANNEL, None)
            messages.success(request, PASSWORD_CHANGED_MESSAGE)
            if outcome.email_removed:
                messages.info(request, EMAIL_REMOVED_MESSAGE)
            return redirect("account_login")

    return render(
        request,
        "accounts/reset_password.html",
        {"form": form, "text": auth_text()},
    )


@login_required(login_url=LOGIN_URL)
@require_http_methods(["GET"])
@never_cache
def account_home(request: HttpRequest) -> HttpResponse:
    """The customer's account home with the masked contacts and links (D73)."""
    # login_required has already refused a guest; the cast only tells mypy.
    user = cast(User, request.user)
    has_email = bool(user.email)
    return render(
        request,
        "accounts/account_home.html",
        {
            "text": auth_text(),
            "account_name": user.full_name or str(ACCOUNT_NAME_FALLBACK),
            "masked_phone": mask_phone(user.phone),
            "masked_email": mask_email(user.email) if user.email else "",
            "phone_verified": user.phone_verified_at is not None,
            "email_verified": user.email_verified_at is not None,
            "has_email": has_email,
            "show_verify_phone": user.phone_verified_at is None,
            "show_verify_email": has_email and user.email_verified_at is None,
            "verify_phone_label": str(BANNER_PHONE),
            "verify_email_label": str(BANNER_EMAIL),
            "password_link": (
                str(SET_PASSWORD_LINK)
                if not user.has_usable_password()
                else str(CHANGE_PASSWORD_LINK)
            ),
            "email_link": str(CHANGE_EMAIL_LINK) if has_email else str(ADD_EMAIL_LINK),
            "orders_url": reverse("order_list"),
            "addresses_url": reverse("address_list"),
            "verify_phone_url": reverse("account_verify_phone"),
            "verify_email_url": reverse("account_verify_email"),
            "password_url": reverse("account_password"),
            "email_url": reverse("account_email"),
        },
    )


@login_required(login_url=LOGIN_URL)
@require_http_methods(["GET", "POST"])
@never_cache
def account_password(request: HttpRequest) -> HttpResponse:
    """Set a first password, or change an existing one (D73)."""
    user = cast(User, request.user)
    require_current = user.has_usable_password()
    form = ChangePasswordForm(
        request.POST or None,
        user=user,
        require_current=require_current,
    )

    # A locked account is refused before its current password is checked, so a
    # correct password cannot bypass the throttle.
    if request.method == "POST" and require_current and login_locked(user):
        messages.error(request, LOGIN_LOCKED_MESSAGE)
    elif request.method == "POST" and form.is_valid():
        if require_current and not user.check_password(form.cleaned_data["current_password"]):
            record_login_failure(user)
            messages.error(request, CURRENT_PASSWORD_WRONG_MESSAGE)
        else:
            user.set_password(form.cleaned_data["new_password1"])
            user.save(update_fields=["password", "updated_at"])
            # Without this the session still holds the old password hash and
            # the customer would be logged out on the next request.
            update_session_auth_hash(request, user)
            clear_login_failures(user)
            messages.success(request, PASSWORD_SAVED_MESSAGE)
            return redirect("account_home")

    return render(
        request,
        "accounts/account_password.html",
        {
            "form": form,
            "text": auth_text(),
            "require_current": require_current,
            "heading": str(
                PASSWORD_PAGE_HEADING_CHANGE if require_current else PASSWORD_PAGE_HEADING_SET
            ),
        },
    )


@login_required(login_url=LOGIN_URL)
@require_http_methods(["GET", "POST"])
@never_cache
def account_email(request: HttpRequest) -> HttpResponse:
    """Add or change the email address and send the customer to verify it (D73)."""
    user = cast(User, request.user)
    require_current = user.has_usable_password()
    # The address on the account is pre-filled, so the customer edits it
    # instead of retyping it.
    form = ChangeEmailForm(
        request.POST or None,
        initial={"email": user.email or ""},
        require_current=require_current,
    )

    if request.method == "POST" and require_current and login_locked(user):
        messages.error(request, LOGIN_LOCKED_MESSAGE)
    elif request.method == "POST" and form.is_valid():
        if require_current and not user.check_password(form.cleaned_data["current_password"]):
            record_login_failure(user)
            messages.error(request, CURRENT_PASSWORD_WRONG_MESSAGE)
        else:
            new_email = form.cleaned_data["email"]
            if new_email == (user.email or ""):
                messages.info(request, EMAIL_UNCHANGED_MESSAGE)
                return redirect("account_email")
            # Another active customer may not take the address; a staff account
            # is a separate space and does not count (D72).
            taken = (
                User.objects.filter(
                    is_staff=False,
                    is_superuser=False,
                    is_active=True,
                    email=new_email,
                )
                .exclude(pk=user.pk)
                .exists()
            )
            if taken:
                messages.error(request, EMAIL_UNUSABLE_MESSAGE)
            else:
                user.email = new_email
                user.email_verified_at = None
                user.save(update_fields=["email", "email_verified_at", "updated_at"])
                # A code asked for the old address must not verify the new one.
                invalidate_email_codes(user)
                messages.success(request, EMAIL_SAVED_MESSAGE)
                return redirect("account_verify_email")

    heading = EMAIL_PAGE_HEADING_CHANGE if user.email else EMAIL_PAGE_HEADING_ADD
    return render(
        request,
        "accounts/account_email.html",
        {
            "form": form,
            "text": auth_text(),
            "require_current": require_current,
            "heading": str(heading),
        },
    )
