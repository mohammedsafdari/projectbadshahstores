"""Credit dues screens for staff under ``/staff/credit/`` (D79 amendment).

The dues list, the two-step payment entry and the manual reminder. Every view
is gated by ``staff_required`` (D75), and the payment itself is written only by
``apps.credit.services.record_payment``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from django import forms
from django.contrib import messages
from django.db.models import Max, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.capabilities import Capability, has_capability
from apps.accounts.models import User
from apps.credit.models import CreditAccount, CreditEntry
from apps.credit.services import CreditError, record_payment
from apps.notifications.order_notices import notify_credit_reminder
from apps.orders.staff_views import staff_nav, staff_required

PAYMENT_RECORDED_MESSAGE = _("The payment was recorded.")
NO_BALANCE_MESSAGE = _("This customer has no pending balance.")
REMINDER_SENT_MESSAGE = _("The reminder was sent.")
REMINDER_FAILED_MESSAGE = _("The reminder could not be sent. Check the SMS settings.")


class RecordPaymentForm(forms.Form):
    """The staff payment form: an amount above zero, a mode and a date."""

    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label=_("Amount received"),
        error_messages={
            "min_value": _("Enter an amount above zero."),
            "invalid": _("Enter the amount as a number."),
        },
    )
    mode = forms.ChoiceField(
        choices=CreditEntry.PaymentMode.choices,
        initial=CreditEntry.PaymentMode.CASH,
        label=_("Payment mode"),
    )
    # ``initial`` may be a callable, so the form never holds a stale date.
    entry_date = forms.DateField(
        required=False,
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}),
        label=_("Payment date"),
    )
    reference = forms.CharField(max_length=100, required=False, label=_("Reference"))
    note = forms.CharField(max_length=200, required=False, label=_("Note"))


def _money(value: Decimal) -> str:
    """One amount with the rupee sign and two places."""
    return f"₹{value:.2f}"


def _last_payment_text(account: CreditAccount) -> str:
    """The date of the newest payment, or an empty string when there is none.

    ``last_payment_date`` is an annotation added by ``dues_list``, so mypy
    cannot see it on the model and the value is read through ``Any``.
    """
    last = cast(Any, account).last_payment_date
    return date_format(last, "j M Y") if last is not None else ""


def _account(account_id: int) -> CreditAccount:
    """One credit account with its customer, or a 404."""
    account = CreditAccount.objects.select_related("user").filter(pk=account_id).first()
    if account is None:
        raise Http404("Credit account not found.")
    return account


@staff_required(Capability.VIEW_CREDIT_DUES)
@require_http_methods(["GET"])
@never_cache
def dues_list(request: HttpRequest) -> HttpResponse:
    """Every customer who still owes money, largest balance first (D79)."""
    user = cast(User, request.user)
    accounts = (
        CreditAccount.objects.filter(balance__gt=0)
        .select_related("user")
        .annotate(
            last_payment_date=Max(
                "entries__entry_date",
                filter=Q(entries__entry_type=CreditEntry.EntryType.PAYMENT),
            ),
        )
        .order_by("-balance", "pk")
    )
    rows = [
        {
            "account": account,
            "customer": account.user,
            "name": account.user.full_name or account.user.phone,
            "phone": account.user.phone,
            "balance_text": _money(account.balance),
            "limit_text": _money(account.credit_limit),
            "last_payment_text": _last_payment_text(account),
            "verified": account.user.is_ready_for_delivery,
            "payment_url": reverse("staff_record_credit_payment", args=[account.pk]),
            "reminder_url": reverse("staff_send_credit_reminder", args=[account.pk]),
        }
        for account in accounts
    ]
    return render(
        request,
        "staff/dues.html",
        {
            "rows": rows,
            "can_record_payment": has_capability(user, Capability.RECORD_CREDIT_PAYMENT),
            **staff_nav(user),
        },
    )


def _record_context(
    account: CreditAccount,
    form: RecordPaymentForm,
    *,
    stage: str,
    error: str | None = None,
) -> dict[str, Any]:
    """The values the payment form and its confirm step print."""
    return {
        "account": account,
        "customer": account.user,
        "form": form,
        "stage": stage,
        "error": error,
        "balance_text": _money(account.balance),
        "limit_text": _money(account.credit_limit),
        "dues_url": reverse("staff_credit_dues"),
        "action_url": reverse("staff_record_credit_payment", args=[account.pk]),
    }


@staff_required(Capability.RECORD_CREDIT_PAYMENT)
@require_http_methods(["GET", "POST"])
@never_cache
def record_payment_view(request: HttpRequest, account_id: int) -> HttpResponse:
    """Enter a payment, show the confirm step, then save it (D79).

    Nothing is written until the staff member confirms the customer and the
    amount on the second step.
    """
    user = cast(User, request.user)
    account = _account(account_id)
    if request.method == "GET":
        return render(
            request,
            "staff/record_payment.html",
            _record_context(account, RecordPaymentForm(), stage="form"),
        )

    form = RecordPaymentForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "staff/record_payment.html",
            _record_context(account, form, stage="form"),
        )
    data = form.cleaned_data
    entry_date = data["entry_date"] or timezone.localdate()
    if request.POST.get("confirm") != "1":
        return render(
            request,
            "staff/record_payment.html",
            _record_context(account, form, stage="confirm"),
        )
    try:
        record_payment(
            account.user,
            data["amount"],
            mode=data["mode"],
            entry_date=entry_date,
            reference=data["reference"],
            note=data["note"],
            actor=user,
        )
    except CreditError as error:
        return render(
            request,
            "staff/record_payment.html",
            _record_context(account, form, stage="confirm", error=str(error)),
        )
    messages.success(request, PAYMENT_RECORDED_MESSAGE)
    return redirect("staff_credit_dues")


@staff_required(Capability.VIEW_CREDIT_DUES)
@require_POST
@never_cache
def send_reminder(request: HttpRequest, account_id: int) -> HttpResponse:
    """Send one manual reminder to a customer who owes money (D79).

    Nothing is sent automatically: a person presses the button. The text goes
    through the existing notification abstraction, and the console backend
    prints it until the DLT registration exists.
    """
    account = _account(account_id)
    if account.balance <= Decimal("0.00"):
        messages.error(request, NO_BALANCE_MESSAGE)
        return redirect("staff_credit_dues")
    if notify_credit_reminder(account.user, account.balance):
        messages.success(request, REMINDER_SENT_MESSAGE)
    else:
        messages.error(request, REMINDER_FAILED_MESSAGE)
    return redirect("staff_credit_dues")
