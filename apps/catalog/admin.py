"""Django admin setup for the catalog app."""

import base64
import hashlib
import os
from typing import Any

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet
from django.forms import FileField, Form
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import URLPattern, path, reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.catalog.audit import (
    audit_actor,
    record_variant_change,
    record_variant_formset_changes,
    snapshot_variant_from_db,
    snapshot_variants_before_formset_save,
)
from apps.catalog.csv_import import MAX_FILE_BYTES, Issue, parse_variants_csv
from apps.catalog.csv_import_apply import ImportRejected, apply_import
from apps.catalog.csv_import_plan import CREATE, UPDATE, RowPlan, build_plan

from .csv_export import variants_for_export, write_variants_csv
from .models import Brand, Category, Product, ProductImage, ProductVariant


def _csv_download(variants: QuerySet[ProductVariant]) -> HttpResponse:
    """Build the CSV file download for the given variants (Slice 19)."""
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    filename = f"variants-{timezone.localdate():%Y-%m-%d}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    # The file holds cost prices, so browsers and proxies must not keep a copy.
    response["Cache-Control"] = "no-store"
    # An HttpResponse works like a file: write() adds text to the download.
    write_variants_csv(variants_for_export(variants), response)
    return response


# Who may use the "Import CSV" page (ADR D47, point 7): the user must be able to do
# the same work by hand, so all four add/change permissions are required. Superusers
# hold every permission, so they pass without a special case.
IMPORT_PERMISSIONS: tuple[str, ...] = (
    "catalog.add_product",
    "catalog.change_product",
    "catalog.add_productvariant",
    "catalog.change_productvariant",
)

# The preview shows this many problems and rows; anything beyond that is only counted.
MAX_REPORTED_ISSUES = 200
MAX_REPORTED_ROWS = 50

# The uploaded file waits here between the preview and the click on Confirm.
IMPORT_SESSION_KEY = "catalog_import"

IMPORT_TEMPLATE = "admin/catalog/productvariant/import_csv.html"


class ImportCsvForm(Form):
    """The upload form: one file field. A plain Form is enough here."""

    csv_file = FileField(label="CSV file")


class VariantCsvExportPermissionMixin:
    """Who may run the CSV export actions.

    The file shows what the variant pages show (cost price included), so the
    rule is the same: the user needs the view permission for variants. Django
    finds this method by name: an action declared with ``permissions=["export"]``
    is offered only when ``has_export_permission`` returns True.
    """

    def has_export_permission(self, request: HttpRequest) -> bool:
        return request.user.has_perm("catalog.view_productvariant")


class ProductImageInline(admin.TabularInline):
    """Photos for a product, edited on the product page."""

    # extra = 0: no blank "add another" rows on the product page.
    model = ProductImage
    extra = 0
    # Fields shown for each inline row, in this order.
    fields = ("image", "preview", "alt_text", "sort_order")
    # readonly_fields is the Django admin idiom for "show this, but do not
    # let staff edit it". Here it holds the computed thumbnail.
    readonly_fields = ("preview",)

    @admin.display(description="Preview")
    def preview(self, obj):
        # @admin.display sets the column header for this method in the admin.
        if not obj.pk or not obj.image:
            # Not saved yet: there is no file, so no thumbnail to show.
            return "-"
        # format_html escapes every value placed into {} (the URL and the alt
        # text), so a malicious file name or alt text cannot inject HTML.
        # The outer quotes are single so the double quotes inside are fine.
        return format_html(
            '<img src="{}" alt="{}" style="height:80px;">',
            obj.image.url,
            obj.alt_text,
        )


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    # Fills the slug box automatically while typing the name.
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "parent", "slug", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


class ProductVariantInline(admin.TabularInline):
    """Variants (pack sizes) edited on the product page."""

    model = ProductVariant
    extra = 0
    fields = (
        "sku_code",
        "barcode",
        "unit",
        "pack_size",
        "is_sold_loose",
        "quantity_step",
        "low_stock_threshold",
        "mrp",
        "selling_price",
        "hsn_code",
        "gst_rate_percent",
        "is_active",
    )


@admin.register(Product)
class ProductAdmin(VariantCsvExportPermissionMixin, admin.ModelAdmin):
    list_display = (
        "name_en",
        "name_hi",
        "brand",
        "category",
        "is_own_brand",
        "diet_type",
        "is_active",
    )
    list_filter = ("is_own_brand", "diet_type", "is_active", "brand", "category")
    search_fields = (
        "name_en",
        "name_hi",
        "slug",
        "search_keywords",
        "variants__sku_code",
        "variants__barcode",
    )
    prepopulated_fields = {"slug": ("name_en",)}
    autocomplete_fields = ("brand", "category")
    list_select_related = ("brand", "category")
    # Inlines appear on the product change page, below the product fields.
    # ProductImageInline comes after ProductVariantInline so variants stay first.
    inlines = [ProductVariantInline, ProductImageInline]
    actions = ["export_variants_csv"]

    @admin.action(
        description="Export all variants of the selected products to CSV",
        permissions=["export"],
    )
    def export_variants_csv(
        self, request: HttpRequest, queryset: QuerySet[Product]
    ) -> HttpResponse:
        # The selected rows are products; the file has one row per variant.
        return _csv_download(ProductVariant.objects.filter(product__in=queryset))

    # save_formset runs once for each inline (variants, then images). Only the
    # variants formset is audited. The old values are read from the database
    # before the save, because the forms have already changed their instances.
    # Django's admin wraps the whole save in one transaction, so the variant
    # edits and their audit rows are saved together or not at all.
    def save_formset(self, request: HttpRequest, form: Any, formset: Any, change: bool) -> None:
        if formset.model is not ProductVariant:
            super().save_formset(request, form, formset, change)
            return
        old_by_pk = snapshot_variants_before_formset_save(formset)
        super().save_formset(request, form, formset, change)
        record_variant_formset_changes(
            actor=audit_actor(request), formset=formset, old_by_pk=old_by_pk
        )


@admin.register(ProductVariant)
class ProductVariantAdmin(VariantCsvExportPermissionMixin, admin.ModelAdmin):
    list_display = (
        "sku_code",
        "product",
        "unit",
        "pack_size",
        "is_sold_loose",
        "mrp",
        "selling_price",
        "is_active",
    )
    list_filter = ("is_active", "is_sold_loose", "unit", "product__is_own_brand")
    search_fields = ("sku_code", "barcode", "product__name_en", "product__name_hi")
    autocomplete_fields = ("product",)
    list_select_related = ("product",)
    actions = ["export_csv"]

    @admin.action(description="Export the selected variants to CSV", permissions=["export"])
    def export_csv(self, request: HttpRequest, queryset: QuerySet[ProductVariant]) -> HttpResponse:
        return _csv_download(queryset)

    # By the time save_model runs, the form has already put the new values on
    # ``obj``, so the old values must be read from the database first. Django's
    # admin wraps the whole save in one transaction, so the variant and its
    # audit row are saved together or not at all.
    def save_model(
        self, request: HttpRequest, obj: ProductVariant, form: Any, change: bool
    ) -> None:
        old = snapshot_variant_from_db(obj.pk) if change else None
        super().save_model(request, obj, form, change)
        if old is not None:
            record_variant_change(actor=audit_actor(request), variant=obj, old=old)

    # -- "Import CSV": upload, read the report, then confirm (D47 points 1 and 5) --
    def get_urls(self) -> list[URLPattern]:
        # Custom pages must come first: the default patterns include
        # ``<path:object_id>/``, which would otherwise swallow this path.
        custom = [
            path(
                "import-csv/",
                self.admin_site.admin_view(self.import_csv_view),
                name="catalog_productvariant_import_csv",
            ),
        ]
        return custom + super().get_urls()

    def import_csv_view(self, request: HttpRequest) -> HttpResponse:
        """Upload a file and show what it would do, then write it on Confirm.

        The preview writes nothing. The Confirm post calls ``apply_import``, which
        locks, plans again and refuses when the data or the file changed since the
        preview (D47 point 5).
        """
        self._require_import_permissions(request)
        if request.method == "POST" and request.POST.get("confirm"):
            return self._confirm_import(request)
        if request.method == "POST":
            return self._preview_import(request)
        return self._render_import(request, form=ImportCsvForm())

    def _require_import_permissions(self, request: HttpRequest) -> None:
        # ``admin_view`` already checked that the user may reach the admin at all;
        # this page decides for itself who may use it, like the stock pages do.
        if not all(request.user.has_perm(perm) for perm in IMPORT_PERMISSIONS):
            raise PermissionDenied

    def _preview_import(self, request: HttpRequest) -> HttpResponse:
        form = ImportCsvForm(request.POST, request.FILES)
        if not form.is_valid():
            return self._render_import(request, form=form)

        upload = form.cleaned_data["csv_file"]
        # Check the size before reading, so an oversized upload is refused without
        # ever being loaded into memory.
        if upload.size > MAX_FILE_BYTES:
            megabytes = MAX_FILE_BYTES // (1024 * 1024)
            form.add_error("csv_file", f"The file is larger than {megabytes} MB.")
            return self._render_import(request, form=form)

        raw = upload.read()  # bytes only: the contents are never printed or logged
        plan = build_plan(parse_variants_csv(raw))
        counts = plan.counts()
        report = self._report(
            ok=plan.ok,
            counts=counts,
            issues=plan.issues,
            rows=plan.rows,
            row_count=plan.row_count,
        )

        if plan.ok and counts[CREATE] + counts[UPDATE] > 0:
            request.session[IMPORT_SESSION_KEY] = {
                # basename() drops any folder part the browser sent with the name.
                "name": os.path.basename(upload.name or "")[:100],
                "b64": base64.b64encode(raw).decode("ascii"),
                "sha256": plan.sha256,
                "fingerprint": plan.fingerprint(),
            }
            report["can_confirm"] = True
        else:
            # Nothing to confirm, so an older file must not stay ready by mistake.
            request.session.pop(IMPORT_SESSION_KEY, None)
        return self._render_import(request, form=ImportCsvForm(), report=report)

    def _confirm_import(self, request: HttpRequest) -> HttpResponse:
        # pop() removes the entry whatever happens next: one upload, one try.
        entry = request.session.pop(IMPORT_SESSION_KEY, None)
        if not entry:
            return self._upload_again(request)
        try:
            raw = base64.b64decode(entry["b64"])
        except (KeyError, TypeError, ValueError):
            return self._upload_again(request)
        # The stored hash guards against a session entry that no longer matches
        # the bytes it claims to hold.
        if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
            return self._upload_again(request)

        try:
            result = apply_import(
                raw,
                # ``request.user`` is typed as "user or anonymous"; the helper narrows
                # it to a real user (None only if the request has none).
                actor=audit_actor(request),
                filename=entry.get("name", ""),
                expected_fingerprint=entry.get("fingerprint"),
            )
        except ImportRejected as error:
            # Nothing was written. Show the problems in the same shape as the preview.
            report = self._report(ok=False, counts={}, issues=error.issues, rows=[], row_count=0)
            return self._render_import(request, form=ImportCsvForm(), report=report)

        self.message_user(
            request,
            f"Imported {result.created} new variant(s), {result.updated} updated, "
            f"{result.unchanged} unchanged; {result.products_created} product(s) "
            f"created, {result.products_updated} updated.",
            messages.SUCCESS,
        )
        return HttpResponseRedirect(reverse("admin:catalog_productvariant_changelist"))

    def _upload_again(self, request: HttpRequest) -> HttpResponse:
        self.message_user(request, "Upload the file again.", messages.WARNING)
        return HttpResponseRedirect(reverse("admin:catalog_productvariant_import_csv"))

    def _report(
        self,
        *,
        ok: bool,
        counts: dict[str, int],
        issues: list[Issue],
        rows: list[RowPlan],
        row_count: int,
    ) -> dict[str, Any]:
        """What the template shows, cut to the first N problems and rows.

        Slicing a list (``items[:200]``) gives a new list with at most that many
        entries, so a file with 4,000 problems cannot make the page enormous.
        """
        shown_issues = issues[:MAX_REPORTED_ISSUES]
        shown_rows = rows[:MAX_REPORTED_ROWS]
        return {
            "ok": ok,
            "counts": counts,
            "issues": shown_issues,
            "more_issues": len(issues) - len(shown_issues),
            "rows": shown_rows,
            "more_rows": len(rows) - len(shown_rows),
            "row_count": row_count,
        }

    def _render_import(
        self, request: HttpRequest, *, form: ImportCsvForm, report: dict[str, Any] | None = None
    ) -> HttpResponse:
        context = {
            # each_context adds the admin's own variables (site header, user links, ...).
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Import variants from CSV",
            "form": form,
            "report": report,
        }
        return TemplateResponse(request, IMPORT_TEMPLATE, context)
