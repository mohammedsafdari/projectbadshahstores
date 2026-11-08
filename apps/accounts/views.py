"""Accounts app views: password login, phone login, sign-up and logout (D59, D72).

The views stay thin: the forms shape the input, ``apps.accounts.otp`` holds the
code rules and ``apps.accounts.passwords`` the password rules. Only Django's
session, messages and login/logout helpers appear here.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import login, logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.auth_presentation import (
    LOGIN_ERROR_MESSAGE,
    LOGIN_LOCKED_MESSAGE,
    SIGNUP_DONE_MESSAGE,
    SIGNUP_EXISTS_MESSAGE,
    auth_text,
)
from apps.accounts.forms import OtpVerifyForm, PasswordLoginForm, PhoneLoginForm, SignupForm
from apps.accounts.models import User
from apps.accounts.otp import request_code, verify_code
from apps.accounts.passwords import (
    REASON_LOCKED,
    REASON_OK,
    AccountExistsError,
    authenticate_customer_password,
    register_customer,
)
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

    Only the customer account space is looked at (D72): a phone that belongs to
    a staff account gets its own separate customer account and the staff row is
    never touched. A new phone creates a MEMBER with an unusable password and
    no staff flags. A deactivated customer returns None: deactivation must not
    be bypassed by creating a fresh account on the same phone.
    """
    user = User.objects.filter(
        phone=phone,
        is_active=True,
        is_staff=False,
        is_superuser=False,
    ).first()
    if user is not None:
        return user
    # The phone may still belong to a deactivated customer; never create a new
    # active customer over it. A staff row with this phone does not block a
    # customer account, because the two spaces are separate (D72).
    if User.objects.filter(phone=phone, is_staff=False, is_superuser=False).exists():
        return None
    return User.objects.create_user(phone=phone)


def _finish_customer_login(
    request: HttpRequest,
    user: User,
    next_url: str | None,
    notice: str | None = None,
) -> HttpResponse:
    """Log a customer in and finish the shared steps (D72).

    The guest cart is read before ``login()`` because logging in cycles the
    session key, then it is merged and cleared. One notice covers the takeover
    message and the cart-merge notice, and the redirect honours the safe next.
    """
    # Read before login(): the merge needs the guest quantities as they were
    # before Django changed the session key (D59, D60).
    guest_items = read_guest_items(request.session)
    login(request, user)
    if notice is not None:
        messages.info(request, notice)
    request.session.pop(SESSION_PHONE_KEY, None)
    merge = merge_guest_cart(user, guest_items)
    clear_guest_cart(request.session)
    if merge.needs_notice:
        messages.info(request, CART_MERGE_MESSAGE)
    return redirect(next_url or "home")


@never_cache
@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    """Show the password form and the code form, and start either flow (D72)."""
    # A logged-in customer does not need the page. A staff person who is in
    # /admin/ may open it: logging in as a customer replaces the session user,
    # so the two identities never share one session (D72).
    if request.user.is_authenticated and not request.user.is_staff:
        return redirect("home")

    next_url = _allowed_next(
        request,
        request.POST.get("next") or request.GET.get("next"),
    )
    method = request.POST.get("method", "otp")
    password_form = PasswordLoginForm()
    otp_form = PhoneLoginForm()

    if request.method == "POST":
        if method == "password":
            password_form = PasswordLoginForm(request.POST)
            if password_form.is_valid():
                login_result = authenticate_customer_password(
                    password_form.cleaned_data["identifier"],
                    password_form.cleaned_data["password"],
                )
                if login_result.reason == REASON_OK and login_result.user is not None:
                    return _finish_customer_login(request, login_result.user, next_url)
                if login_result.reason == REASON_LOCKED:
                    messages.error(request, LOGIN_LOCKED_MESSAGE)
                else:
                    messages.error(request, LOGIN_ERROR_MESSAGE)
        else:
            # A POST without "method" is the old code form, so every existing
            # code test keeps working (D72).
            otp_form = PhoneLoginForm(request.POST)
            if otp_form.is_valid():
                phone = otp_form.cleaned_data["phone"]
                otp_result = request_code(phone)
                if otp_result.sent:
                    request.session[SESSION_PHONE_KEY] = phone
                    if next_url is not None:
                        request.session[SESSION_NEXT_KEY] = next_url
                    else:
                        request.session.pop(SESSION_NEXT_KEY, None)
                    return redirect("account_verify")
                # A rate-limited request is the same for known and unknown phones.
                messages.error(request, RATE_LIMITED_MESSAGE)

    return render(
        request,
        "accounts/login.html",
        {
            # "form" stays the code form, so the old template contract and the
            # existing code tests keep working.
            "form": otp_form,
            "password_form": password_form,
            "next_url": next_url or "",
            "text": auth_text(),
        },
    )


@never_cache
@require_http_methods(["GET", "POST"])
def signup_view(request: HttpRequest) -> HttpResponse:
    """Create a customer account, log it in and send it home (D72)."""
    if request.user.is_authenticated and not request.user.is_staff:
        return redirect("home")

    next_url = _allowed_next(
        request,
        request.POST.get("next") or request.GET.get("next"),
    )
    # ``request.POST or None`` makes the form bound only on a POST; on a GET it
    # is unbound and shows empty fields.
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user = register_customer(
                phone=form.cleaned_data["phone"],
                email=form.cleaned_data["email"],
                full_name=form.cleaned_data["full_name"],
                password=form.cleaned_data["password1"],
            )
        except AccountExistsError:
            # One generic sentence: never say which of the two details matched.
            form.add_error(None, SIGNUP_EXISTS_MESSAGE)
        else:
            messages.success(request, SIGNUP_DONE_MESSAGE)
            return _finish_customer_login(request, user, next_url)

    return render(
        request,
        "accounts/signup.html",
        {"form": form, "next_url": next_url or "", "text": auth_text()},
    )


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
                    # usable password was cleared or an email was removed, so
                    # the customer is told.
                    claimed = claim_account_by_otp_login(user)
                    next_url = _allowed_next(
                        request,
                        request.session.pop(SESSION_NEXT_KEY, None),
                    )
                    return _finish_customer_login(
                        request,
                        user,
                        next_url,
                        str(PASSWORD_RESET_FOR_SAFETY_MESSAGE) if claimed else None,
                    )
            messages.error(request, CODE_ERROR_MESSAGE)
    else:
        form = OtpVerifyForm()

    return render(request, "accounts/verify.html", {"form": form, "phone": phone})


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    """Log out on POST only, so a link cannot end the session by accident."""
    logout(request)
    return redirect("home")
