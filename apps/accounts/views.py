"""Accounts app views: phone login, code check and logout (D59).

The views stay thin: the forms shape the input and ``apps.accounts.otp`` holds
the code and rate-limit rules. Only Django's session, messages and login/logout
helpers appear here.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import login, logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.accounts.forms import OtpVerifyForm, PhoneLoginForm
from apps.accounts.models import User
from apps.accounts.otp import request_code, verify_code
from apps.accounts.verification import claim_account_by_otp_login
from apps.accounts.verification_presentation import PASSWORD_RESET_FOR_SAFETY_MESSAGE
from apps.cart.services import merge_guest_cart
from apps.cart.stores import clear_guest_cart, read_guest_items

# The phone waiting for its code, and an optional page to return to after login.
SESSION_PHONE_KEY = "otp_phone"
SESSION_NEXT_KEY = "otp_next"

RATE_LIMITED_MESSAGE = _("Too many codes requested for this number. Please try again later.")
# One generic message for a wrong code and for an account that may not use OTP,
# so the page never says whether a phone belongs to staff (D59).
CODE_ERROR_MESSAGE = _("That code is not valid. Please try again.")
# One notice for any line the merge clamped or dropped, never per item (D60).
CART_MERGE_MESSAGE = _("Some items in your cart were adjusted to what we have in stock.")


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


def _customer_for_login(phone: str) -> User | None:
    """The customer behind a verified phone, or None when OTP login is refused.

    A new phone creates a MEMBER with an unusable password and no staff flags.
    A staff or inactive account returns None: staff log in with a password at
    /admin only, and a deactivated account must not come back through OTP.
    """
    user = User.objects.filter(phone=phone, is_active=True).first()
    if user is not None:
        if user.is_staff or user.is_superuser:
            return None
        return user
    # The phone may still belong to a deactivated account; never create a new
    # active customer over it, or deactivation would be easy to bypass.
    if User.objects.filter(phone=phone).exists():
        return None
    return User.objects.create_user(phone=phone)


def login_view(request: HttpRequest) -> HttpResponse:
    """Ask for a mobile number and start the code flow."""
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        form = PhoneLoginForm(request.POST)
        if form.is_valid():
            phone = form.cleaned_data["phone"]
            next_url = _allowed_next(
                request,
                request.POST.get("next") or request.GET.get("next"),
            )
            result = request_code(phone)
            if result.sent:
                request.session[SESSION_PHONE_KEY] = phone
                if next_url is not None:
                    request.session[SESSION_NEXT_KEY] = next_url
                else:
                    request.session.pop(SESSION_NEXT_KEY, None)
                return redirect("account_verify")
            # A rate-limited request is the same for known and unknown phones.
            messages.error(request, RATE_LIMITED_MESSAGE)
    else:
        form = PhoneLoginForm()

    return render(request, "accounts/login.html", {"form": form})


def verify_view(request: HttpRequest) -> HttpResponse:
    """Check the code and log the customer in."""
    phone = request.session.get(SESSION_PHONE_KEY)
    if not phone:
        # No code was requested in this session, so there is nothing to check.
        return redirect("account_login")

    if request.method == "POST":
        form = OtpVerifyForm(request.POST)
        if form.is_valid():
            result = verify_code(phone, form.cleaned_data["code"])
            if result.ok and result.phone is not None:
                user = _customer_for_login(result.phone)
                if user is not None:
                    # An OTP login proves the phone, and an account that was
                    # never verified is taken over here (D71). The flag says a
                    # usable password was cleared, so the customer is told.
                    claimed = claim_account_by_otp_login(user)
                    # Read the guest cart before login(): logging in changes
                    # the session key, and the merge needs the guest quantities
                    # as they were before that (D59, D60).
                    guest_items = read_guest_items(request.session)
                    # Django's login() cycles the session key, so a code used
                    # before login cannot keep the old session (D59).
                    login(request, user)
                    if claimed:
                        messages.info(request, PASSWORD_RESET_FOR_SAFETY_MESSAGE)
                    request.session.pop(SESSION_PHONE_KEY, None)
                    merge = merge_guest_cart(user, guest_items)
                    clear_guest_cart(request.session)
                    if merge.needs_notice:
                        messages.info(request, CART_MERGE_MESSAGE)
                    next_url = _allowed_next(
                        request,
                        request.session.pop(SESSION_NEXT_KEY, None),
                    )
                    return redirect(next_url or "home")
            messages.error(request, CODE_ERROR_MESSAGE)
    else:
        form = OtpVerifyForm()

    return render(request, "accounts/verify.html", {"form": form, "phone": phone})


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    """Log out on POST only, so a link cannot end the session by accident."""
    logout(request)
    return redirect("home")
