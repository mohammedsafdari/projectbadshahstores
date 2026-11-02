"""Storefront app views (Slice 23d, refactored in 23f)."""

from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlencode

from django.conf import settings
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.views.generic import TemplateView

from apps.catalog.models import Brand, Category, Product
from apps.storefront.filter_options import brand_options
from apps.storefront.filters import parse_filters
from apps.storefront.product_detail import get_product_detail
from apps.storefront.services import (
    CategoryTree,
    build_cards,
    visible_products,
)


def cut_meta(text: str, limit: int = 160) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut + "\u2026"


SORT_OPTIONS = [
    ("name", "Name"),
    ("price_asc", "Price: low to high"),
    ("price_desc", "Price: high to low"),
    ("newest", "Newest"),
]

PRODUCTS_PER_PAGE = 24


def _querystring_without_page(request: HttpRequest) -> str:
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()


def _normalise_sort(request: HttpRequest) -> str:
    sort = request.GET.get("sort", "name")
    if sort not in {value for value, _ in SORT_OPTIONS}:
        sort = "name"
    return sort


def _wants_partial(request: HttpRequest) -> bool:
    """True when htmx wants only the results block, not the whole page."""
    is_htmx = request.headers.get("HX-Request") == "true"
    # After the Back button htmx asks for the page again and needs all of it.
    is_history_restore = request.headers.get("HX-History-Restore-Request") == "true"
    return is_htmx and not is_history_restore


def _clear_url(request: HttpRequest, sort: str) -> str:
    """This page's address without filters; a non-default sort is kept."""
    if sort == "name":
        return request.path
    return request.path + "?" + urlencode({"sort": sort})


def _render_listing(
    request: HttpRequest,
    *,
    tree: CategoryTree,
    category: Category | None = None,
    brand: Brand | None = None,
    page_title: str,
    breadcrumbs: list[dict[str, str | None]],
    child_categories: list[Category],
) -> HttpResponse:
    sort = _normalise_sort(request)
    filters = parse_filters(request.GET)
    if brand is not None:
        # A brand page is already one brand, so a ?brand= in the address is ignored.
        # replace() makes a copy of the frozen dataclass with one field changed.
        filters = replace(filters, brand_slugs=())
    qs = visible_products(tree=tree, category=category, brand=brand, sort=sort, filters=filters)
    paginator = Paginator(qs, PRODUCTS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page"))
    cards = build_cards(page_obj.object_list)
    context: dict[str, object] = {
        "page_obj": page_obj,
        "cards": cards,
        "current_sort": sort,
        "sort_options": SORT_OPTIONS,
        "querystring": _querystring_without_page(request),
        "breadcrumbs": breadcrumbs,
        "child_categories": child_categories,
        "page_title": page_title,
        "filters": filters,
        "result_count": paginator.count,
        "clear_url": _clear_url(request, sort),
    }
    if _wants_partial(request):
        response = render(request, "storefront/_listing_results.html", context)
    else:
        # The filter panel is not part of the swapped block, so only a full page needs it.
        context["show_brand_filter"] = brand is None
        context["brand_options"] = (
            brand_options(tree=tree, category=category) if brand is None else []
        )
        context["selected_brands"] = set(filters.brand_slugs)
        response = render(request, "storefront/product_list.html", context)
    patch_vary_headers(response, ["HX-Request"])
    return response


class HomeView(TemplateView):
    template_name = "storefront/home.html"


def product_list(request: HttpRequest) -> HttpResponse:
    tree = CategoryTree()
    return _render_listing(
        request,
        tree=tree,
        page_title="All products",
        breadcrumbs=[{"label": "Home", "url": reverse("home")}],
        child_categories=[],
    )


def product_detail(request: HttpRequest, slug: str) -> HttpResponse:
    try:
        detail = get_product_detail(slug, build_url=request.build_absolute_uri)
    except Product.DoesNotExist:
        raise Http404 from None
    product = detail.product
    meta_description = cut_meta(
        product.description or f"{product.name_en} by {product.brand.name} at Badshah Store"
    )
    breadcrumbs: list[dict[str, str | None]] = [{"label": "Home", "url": reverse("home")}]
    for ancestor in CategoryTree().ancestors(product.category):
        breadcrumbs.append(
            {
                "label": ancestor.name,
                "url": reverse("category_detail", args=[ancestor.slug]),
            }
        )
    breadcrumbs.append(
        {
            "label": product.category.name,
            "url": reverse("category_detail", args=[product.category.slug]),
        }
    )
    breadcrumbs.append({"label": product.name_en, "url": None})
    context = {
        "detail": detail,
        "product": product,
        "meta_description": meta_description,
        "page_title": f"{product.name_en} — {product.brand.name} — Badshah Store",
        "breadcrumbs": breadcrumbs,
        "shop_fssai": getattr(settings, "SHOP_FSSAI_LICENCE", ""),
    }
    return render(request, "storefront/product_detail.html", context)


def category_detail(request: HttpRequest, slug: str) -> HttpResponse:
    tree = CategoryTree()
    category = get_object_or_404(Category, slug=slug, is_active=True)
    for ancestor in tree.ancestors(category):
        if not ancestor.is_active:
            raise Http404
    child_categories = list(Category.objects.filter(parent=category, is_active=True))
    breadcrumbs: list[dict[str, str | None]] = [{"label": "Home", "url": reverse("home")}]
    for ancestor in tree.ancestors(category):
        breadcrumbs.append(
            {
                "label": ancestor.name,
                "url": reverse("category_detail", args=[ancestor.slug]),
            }
        )
    breadcrumbs.append({"label": category.name, "url": None})
    return _render_listing(
        request,
        tree=tree,
        category=category,
        page_title=category.name,
        breadcrumbs=breadcrumbs,
        child_categories=child_categories,
    )


def brand_detail(request: HttpRequest, slug: str) -> HttpResponse:
    brand = get_object_or_404(Brand, slug=slug, is_active=True)
    tree = CategoryTree()
    breadcrumbs: list[dict[str, str | None]] = [
        {"label": "Home", "url": reverse("home")},
        {"label": brand.name, "url": None},
    ]
    return _render_listing(
        request,
        tree=tree,
        brand=brand,
        page_title=brand.name,
        breadcrumbs=breadcrumbs,
        child_categories=[],
    )
