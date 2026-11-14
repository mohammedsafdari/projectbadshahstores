"""The staff report screens under ``/staff/reports/`` (D85).

Every view stays thin: it reads the query string, asks ``queries.py`` or
``cost_prices.py`` for the numbers and renders a template. ``staff_required``
sends a guest to the shop login with ``next`` and refuses a signed-in person who
lacks the capability with a plain 403 page (D75).

The two capabilities are checked before anything is read: every report page and
every CSV download needs ``VIEW_REPORTS``, and the cost price upload needs
``MANAGE_COST_PRICES``, so cost and margin figures only ever reach someone
allowed to see them.
"""

from __future__ import annotations

import base64
import os
from typing import Any, cast

from django import forms
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts.capabilities import Capability, has_capability
from apps.accounts.models import User
from apps.orders.staff_views import staff_nav, staff_required
from apps.reports import csv_output
from apps.reports.cost_prices import (
    CostApplyResult,
    CostPriceError,
    CostPricePlan,
    CostPriceRejected,
    apply_cost_prices,
    plan_cost_prices,
)
from apps.reports.queries import (
    GST_NOTE,
    DateRange,
    cod_and_credit_report,
    gst_report,
    own_brand_report,
    parse_range,
    sales_reports,
    stock_valuation_report,
)

# The uploaded file waits here between the preview and the click on Apply.
COST_SESSION_KEY = "reports_cost_prices"

COST_UPLOAD_TEMPLATE = "staff/reports/cost_prices.html"

# The sentences the cost price page shows.
UPLOAD_NEEDED = _("Choose a CSV file to upload.")
APPLIED = _("Saved %(updated)s cost price(s); %(unchanged)s row(s) were unchanged.")
NOTHING_TO_APPLY = _("Upload a file and check the plan before applying it.")


class CostCsvForm(forms.Form):
    """The upload form: one file field. A plain ``Form`` is enough here."""

    csv_file = forms.FileField(label=_("CSV file"))


def _current_user(request: HttpRequest) -> User:
    # staff_required has already refused a guest; the cast only tells mypy.
    return cast(User, request.user)


def _period(request: HttpRequest) -> DateRange:
    return parse_range(request.GET.get("from"), request.GET.get("to"))


def _page_context(user: User, request: HttpRequest, period: DateRange) -> dict[str, Any]:
    """The values every report page shares: the staff header and the range form."""
    return {
        "period": period,
        "from_value": request.GET.get("from", "") or "",
        "to_value": request.GET.get("to", "") or "",
        "range_action": request.path,
        **staff_nav(user),
    }


def _csv_response(filename: str) -> HttpResponse:
    """A download response with a plain ASCII file name."""
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _filename(name: str, period: DateRange | None = None) -> str:
    if period is None:
        return f"{name}.csv"
    return f"{name}-{period.start.isoformat()}-to-{period.end.isoformat()}.csv"


def _bad_range(request: HttpRequest, period: DateRange, page: str) -> HttpResponse:
    """Send a bad range back to its page with the sentence, never a 500."""
    messages.error(request, period.error)
    return redirect(page)


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------
@staff_required(Capability.VIEW_REPORTS)
@require_http_methods(["GET"])
@never_cache
def index(request: HttpRequest) -> HttpResponse:
    """The list of reports this person may open."""
    user = _current_user(request)
    return render(
        request,
        "staff/reports/index.html",
        {
            "can_manage_cost_prices": _may_enter_costs(user),
            **staff_nav(user),
        },
    )


def _may_enter_costs(user: User) -> bool:
    """True when this person may open the cost price upload as well."""
    return has_capability(user, Capability.MANAGE_COST_PRICES)


# ---------------------------------------------------------------------------
# 1. Sales
# ---------------------------------------------------------------------------
@staff_required(Capability.VIEW_REPORTS)
@require_http_methods(["GET"])
@never_cache
def sales_page(request: HttpRequest) -> HttpResponse:
    """Sales by day, product, brand and category on one page, in four sections."""
    user = _current_user(request)
    period = _period(request)
    context: dict[str, Any] = _page_context(user, request, period)
    if period.ok:
        context["reports"] = sales_reports(user, period)
    return render(request, "staff/reports/sales.html", context)


@staff_required(Capability.VIEW_REPORTS)
@require_http_methods(["GET"])
@never_cache
def sales_csv(request: HttpRequest) -> HttpResponse:
    """The four sales sections as one CSV file."""
    user = _current_user(request)
    period = _period(request)
    if not period.ok:
        return _bad_range(request, period, "reports_sales")
    response = _csv_response(_filename("sales", period))
    csv_output.write_sales_csv(sales_reports(user, period), response)
    return response


# ---------------------------------------------------------------------------
# 2. Own brand
# ---------------------------------------------------------------------------
@staff_required(Capability.VIEW_REPORTS)
@require_http_methods(["GET"])
@never_cache
def own_brand_page(request: HttpRequest) -> HttpResponse:
    """Own brand against third-party, with the margin at today's cost."""
    user = _current_user(request)
    period = _period(request)
    context: dict[str, Any] = _page_context(user, request, period)
    if period.ok:
        context["report"] = own_brand_report(user, period)
    return render(request, "staff/reports/own_brand.html", context)


@staff_required(Capability.VIEW_REPORTS)
@require_http_methods(["GET"])
@never_cache
def own_brand_csv(request: HttpRequest) -> HttpResponse:
    """The margin report as a CSV file."""
    user = _current_user(request)
    period = _period(request)
    if not period.ok:
        return _bad_range(request, period, "reports_own_brand")
    response = _csv_response(_filename("own-brand", period))
    csv_output.write_own_brand_csv(own_brand_report(user, period), response)
    return response


# ---------------------------------------------------------------------------
# 3. GST
# ---------------------------------------------------------------------------
@staff_required(Capability.VIEW_REPORTS)
@require_http_methods(["GET"])
@never_cache
def gst_page(request: HttpRequest) -> HttpResponse:
    """The GST summary by rate, with the unconfirmed note."""
    user = _current_user(request)
    period = _period(request)
    context: dict[str, Any] = _page_context(user, request, period)
    # The note is shown even when the range is bad, so it can never be missed.
    context["note"] = GST_NOTE
    if period.ok:
        context["report"] = gst_report(user, period)
    return render(request, "staff/reports/gst.html", context)


@staff_required(Capability.VIEW_REPORTS)
@require_http_methods(["GET"])
@never_cache
def gst_csv(request: HttpRequest) -> HttpResponse:
    """The GST summary as a CSV file, with the note as its first row."""
    user = _current_user(request)
    period = _period(request)
    if not period.ok:
        return _bad_range(request, period, "reports_gst")
    response = _csv_response(_filename("gst", period))
    csv_output.write_gst_csv(gst_report(user, period), response)
    return response


# ---------------------------------------------------------------------------
# 4. COD and credit
# ---------------------------------------------------------------------------
@staff_required(Capability.VIEW_REPORTS)
@require_http_methods(["GET"])
@never_cache
def cod_credit_page(request: HttpRequest) -> HttpResponse:
    """The cash at the door and the khata figures for the range."""
    user = _current_user(request)
    period = _period(request)
    context: dict[str, Any] = _page_context(user, request, period)
    if period.ok:
        context["report"] = cod_and_credit_report(user, period)
    return render(request, "staff/reports/cod_credit.html", context)


@staff_required(Capability.VIEW_REPORTS)
@require_http_methods(["GET"])
@never_cache
def cod_credit_csv(request: HttpRequest) -> HttpResponse:
    """The COD and credit figures as a CSV file."""
    user = _current_user(request)
    period = _period(request)
    if not period.ok:
        return _bad_range(request, period, "reports_cod_credit")
    response = _csv_response(_filename("cod-and-credit", period))
    csv_output.write_cod_credit_csv(cod_and_credit_report(user, period), response)
    return response


# ---------------------------------------------------------------------------
# 5. Stock valuation
# ---------------------------------------------------------------------------
@staff_required(Capability.VIEW_REPORTS)
@require_http_methods(["GET"])
@never_cache
def stock_valuation_page(request: HttpRequest) -> HttpResponse:
    """What the stock on the shelf is worth at today's cost, as of now."""
    user = _current_user(request)
    return render(
        request,
        "staff/reports/stock_valuation.html",
        {
            "report": stock_valuation_report(user),
            **staff_nav(user),
        },
    )


@staff_required(Capability.VIEW_REPORTS)
@require_http_methods(["GET"])
@never_cache
def stock_valuation_csv(request: HttpRequest) -> HttpResponse:
    """The stock valuation as a CSV file."""
    user = _current_user(request)
    response = _csv_response(_filename("stock-valuation"))
    csv_output.write_valuation_csv(stock_valuation_report(user), response)
    return response


# ---------------------------------------------------------------------------
# 6. Cost prices
# ---------------------------------------------------------------------------
@staff_required(Capability.MANAGE_COST_PRICES)
@require_http_methods(["GET", "POST"])
@never_cache
def cost_prices_page(request: HttpRequest) -> HttpResponse:
    """Upload a private cost price CSV, check the plan, then apply it."""
    user = _current_user(request)
    if request.method == "POST":
        if request.POST.get("apply"):
            return _apply_upload(request, user)
        return _preview_upload(request, user)
    return _render_upload(request, user)


def _render_upload(
    request: HttpRequest,
    user: User,
    *,
    form: CostCsvForm | None = None,
    plan: CostPricePlan | None = None,
) -> HttpResponse:
    """Show the upload form, and the plan when there is one."""
    counts = plan.counts() if plan is not None else {"UPDATE": 0, "UNCHANGED": 0}
    return render(
        request,
        COST_UPLOAD_TEMPLATE,
        {
            "form": form if form is not None else CostCsvForm(),
            "plan": plan,
            "counts": counts,
            "can_apply": bool(plan is not None and plan.ok and counts["UPDATE"]),
            **staff_nav(user),
        },
    )


def _preview_upload(request: HttpRequest, user: User) -> HttpResponse:
    """Check the file and show what it would do. Writes nothing."""
    form = CostCsvForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, UPLOAD_NEEDED)
        return _render_upload(request, user, form=form)

    upload = form.cleaned_data["csv_file"]
    # Read as bytes only: the contents are never printed or logged.
    raw = upload.read()
    try:
        plan = plan_cost_prices(raw, actor=user)
    except CostPriceError as error:
        messages.error(request, str(error))
        return _render_upload(request, user)

    if plan.ok:
        request.session[COST_SESSION_KEY] = {
            # basename() drops any folder part the browser sent with the name.
            "name": os.path.basename(upload.name or "")[:100],
            "b64": base64.b64encode(raw).decode("ascii"),
            "sha256": plan.sha256,
            "fingerprint": plan.fingerprint(),
        }
    else:
        # Nothing to apply, so an older file must not stay ready by mistake.
        request.session.pop(COST_SESSION_KEY, None)
    return _render_upload(request, user, plan=plan)


def _apply_upload(request: HttpRequest, user: User) -> HttpResponse:
    """Write the file that was previewed, refusing when it or the data moved on."""
    stored = request.session.get(COST_SESSION_KEY)
    if not isinstance(stored, dict) or "b64" not in stored:
        messages.error(request, NOTHING_TO_APPLY)
        return _render_upload(request, user)

    raw = base64.b64decode(stored["b64"])
    try:
        result: CostApplyResult = apply_cost_prices(
            raw,
            actor=user,
            filename=str(stored.get("name", "")),
            expected_fingerprint=str(stored.get("fingerprint", "")),
        )
    except CostPriceRejected as error:
        request.session.pop(COST_SESSION_KEY, None)
        for issue in error.issues:
            messages.error(request, issue.message)
        return _render_upload(request, user, plan=plan_cost_prices(raw, actor=user))

    request.session.pop(COST_SESSION_KEY, None)
    messages.success(
        request,
        APPLIED % {"updated": result.updated, "unchanged": result.unchanged},
    )
    return redirect("reports_cost_prices")
