"""Credit (khata) admin: editable accounts and a read-only ledger (D67 point 13).

Owners and managers may use these screens; a PACKER or DELIVERY staff member
may not. A limit or status change always goes through the services, and the
entry "add" page records a payment or an adjustment through the services too.
The raw models are never saved from here.
"""

from __future__ import annotations

from typing import Any, cast

from django import forms
from django.contrib import admin
from django.contrib.admin import helpers
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from apps.accounts.capabilities import Capability, has_capability
from apps.accounts.models import User

from .models import CreditAccount, CreditEntry
from .services import (
    CreditError,
    available_credit,
    record_adjustment,
    record_payment,
    set_credit_active,
    set_credit_limit,
)


def _audit_actor(request: HttpRequest) -> User | None:
    """The logged-in user behind a request, or None for anything else."""
    user = request.user
    return user if isinstance(user, User) else None


class CreditEntryAdminForm(forms.Form):
    """The add form: staff record payments and adjustments only (D67 point 13).

    A plain Form, not a ModelForm: a payment is typed as a positive number, so
    the model's negative-amount constraints must not run on the raw input. The
    services do the signing and every check.
    """

    account = forms.ModelChoiceField(
        queryset=CreditAccount.objects.select_related("user").order_by("user__phone"),
        label="Customer",
    )
    entry_type = forms.ChoiceField(
        choices=[
            (CreditEntry.EntryType.PAYMENT, "Payment"),
            (CreditEntry.EntryType.ADJUSTMENT, "Adjustment"),
        ],
        label="Entry type",
    )
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text=(
            "A payment is a positive number and is stored as money received. "
            "An adjustment is signed: positive raises the balance, negative lowers it."
        ),
    )
    payment_mode = forms.ChoiceField(
        choices=[("", "---------"), *CreditEntry.PaymentMode.choices],
        required=False,
        label="Payment mode",
    )
    # ``initial`` may be a callable, so the form never holds a stale date.
    entry_date = forms.DateField(
        initial=timezone.localdate,
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        label="Payment date",
    )
    reference = forms.CharField(max_length=100, required=False)
    note = forms.CharField(max_length=200, required=False)


UNVERIFIED_CREDIT_MESSAGE = (
    "The customer must verify the phone number (and the email, if any) before getting credit."
)


class CreditAccountAdminForm(forms.ModelForm):
    """Refuse a positive limit for an unverified customer with a form error.

    The service raises the same refusal, but a form error keeps the admin page
    usable instead of showing a crash page.
    """

    class Meta:
        model = CreditAccount
        fields = ["user", "credit_limit", "balance", "is_active"]

    def clean(self):
        # django-stubs types super().clean() as possibly None; Django itself
        # always returns the cleaned data here.
        cleaned = super().clean() or {}
        user = cleaned.get("user")
        limit = cleaned.get("credit_limit")
        if user is not None and limit is not None and limit > 0 and not user.is_ready_for_delivery:
            raise forms.ValidationError(UNVERIFIED_CREDIT_MESSAGE)
        return cleaned


@admin.register(CreditAccount)
class CreditAccountAdmin(admin.ModelAdmin):
    form = CreditAccountAdminForm
    list_display = (
        "user",
        "user_name",
        "credit_limit",
        "balance",
        "available_credit_display",
        "is_active",
    )
    list_filter = ("is_active",)
    search_fields = ("user__phone", "user__full_name")
    list_select_related = ("user",)
    ordering = ("user__phone",)

    @admin.display(description="Customer name")
    def user_name(self, obj: CreditAccount) -> str:
        return obj.user.full_name

    @admin.display(description="Available credit")
    def available_credit_display(self, obj: CreditAccount) -> str:
        return f"{available_credit(obj.user):.2f}"

    @admin.display(description="Verification")
    def verification_state(self, obj: CreditAccount | None) -> str:
        """Whether this customer may be given credit (D71, D79)."""
        if obj is None:
            # The add page has no saved account yet.
            return ""
        if obj.user.is_ready_for_delivery:
            return "Verified"
        missing: list[str] = []
        if obj.user.phone_verified_at is None:
            missing.append("phone")
        if obj.user.email and obj.user.email_verified_at is None:
            missing.append("email")
        return f"Not verified: {', '.join(missing)}"

    def get_readonly_fields(
        self,
        request: HttpRequest,
        obj: CreditAccount | None = None,
    ) -> tuple[str, ...]:
        # The balance is written only by the services, never by staff; the
        # verification state is read-only information for the person giving
        # credit (D79).
        return ("balance", "verification_state")

    def save_model(
        self,
        request: HttpRequest,
        obj: CreditAccount,
        form: Any,
        change: bool,
    ) -> None:
        """Send a limit or status change through the services (D67 point 13)."""
        actor = _audit_actor(request)
        account = set_credit_limit(obj.user, obj.credit_limit, actor=actor)
        set_credit_active(obj.user, obj.is_active, actor=actor)
        # On the add page ``obj`` is an unsaved form instance; the service
        # saved a different row. Copy its pk so the admin's response can link
        # to the new account instead of crashing on a None pk.
        obj.pk = account.pk

    def has_view_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        # The matrix alone decides who may read the ledger (D75 amendment).
        return has_capability(request.user, Capability.VIEW_CREDIT_DUES)

    def has_module_permission(self, request: HttpRequest) -> bool:
        return has_capability(request.user, Capability.VIEW_CREDIT_DUES)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return has_capability(request.user, Capability.RECORD_CREDIT_PAYMENT)

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return has_capability(request.user, Capability.RECORD_CREDIT_PAYMENT)

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        # Accounts are suspended with ``is_active``, never deleted.
        return False


@admin.register(CreditEntry)
class CreditEntryAdmin(admin.ModelAdmin):
    list_display = (
        "entry_date",
        "account",
        "entry_type",
        "amount",
        "balance_after",
        "order",
        "payment_mode",
        "created_by",
    )
    list_filter = ("entry_type", "payment_mode")
    search_fields = ("account__user__phone", "reference")
    list_select_related = ("account", "account__user", "order", "created_by")
    ordering = ("-entry_date", "-id")
    list_per_page = 50

    def get_readonly_fields(
        self,
        request: HttpRequest,
        obj: CreditEntry | None = None,
    ) -> list[str]:
        # Existing rows are history: no field may be edited.
        if obj is None:
            return []
        return [field.name for field in self.model._meta.fields]

    def add_view(
        self,
        request: HttpRequest,
        form_url: str = "",
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        """The add page: call a service and show its refusal as a form error.

        The standard admin saves after validation and cannot turn a service
        refusal into a form error, so this page renders the one form itself.
        """
        if not self.has_add_permission(request):
            raise PermissionDenied
        form = CreditEntryAdminForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            # A plain form builds no model instance, so save_model copies the
            # saved entry's pk onto this empty one for the admin's response.
            obj = CreditEntry()
            try:
                self.save_model(request, obj, form, change=False)
            except CreditError as error:
                form.add_error(None, str(error))
            else:
                if obj.pk is not None:
                    entry = CreditEntry.objects.get(pk=obj.pk)
                    self.log_addition(request, entry, [{"added": {}}])
                    return self.response_add(request, entry)

        fieldsets = [(None, {"fields": list(form.fields)})]
        # django-stubs types these helpers as ModelForm-only, but they accept
        # any Form at runtime; the cast keeps mypy quiet about the plain Form.
        admin_form = helpers.AdminForm(cast(Any, form), fieldsets, {}, (), model_admin=self)
        context: dict[str, Any] = {
            **self.admin_site.each_context(request),
            "title": f"Add {self.opts.verbose_name}",
            "subtitle": None,
            "adminform": admin_form,
            "object_id": None,
            "original": None,
            "is_popup": False,
            "to_field": None,
            "media": self.media + admin_form.media,
            "inline_admin_formsets": [],
            "errors": helpers.AdminErrorList(cast(Any, form), []),
            "preserved_filters": self.get_preserved_filters(request),
        }
        context.update(extra_context or {})
        return self.render_change_form(
            request,
            context,
            add=True,
            change=False,
            obj=None,
            form_url=form_url,
        )

    def save_model(
        self,
        request: HttpRequest,
        obj: CreditEntry,
        form: Any,
        change: bool,
    ) -> None:
        """Record the entry through the services, never by saving the model."""
        if change:
            return
        data = form.cleaned_data
        actor = _audit_actor(request)
        entry: CreditEntry
        if data["entry_type"] == CreditEntry.EntryType.PAYMENT:
            entry = record_payment(
                data["account"].user,
                data["amount"],
                mode=data["payment_mode"],
                entry_date=data["entry_date"],
                reference=data["reference"],
                note=data["note"],
                actor=actor,
            )
        else:
            entry = record_adjustment(
                data["account"].user,
                data["amount"],
                note=data["note"],
                actor=actor,
            )
        # ``obj`` is only a carrier: the service already saved ``entry``.
        obj.pk = entry.pk

    def has_view_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return has_capability(request.user, Capability.VIEW_CREDIT_DUES)

    def has_module_permission(self, request: HttpRequest) -> bool:
        return has_capability(request.user, Capability.VIEW_CREDIT_DUES)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return has_capability(request.user, Capability.RECORD_CREDIT_PAYMENT)

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        # Existing entries are append-only history.
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
