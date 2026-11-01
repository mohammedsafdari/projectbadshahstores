"""Storefront app views (Slice 23d, refactored in 23f)."""

from __future__ import annotations

from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.generic import TemplateView

from apps.catalog.models import Brand, Category
from apps.storefront.services import (
    CategoryTree,
    build_cards,
    visible_products,
)

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
    qs = visible_products(tree=tree, category=category, brand=brand, sort=sort)
    paginator = Paginator(qs, PRODUCTS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page"))
    cards = build_cards(page_obj.object_list)
    context = {
        "page_obj": page_obj,
        "cards": cards,
        "current_sort": sort,
        "sort_options": SORT_OPTIONS,
        "querystring": _querystring_without_page(request),
        "breadcrumbs": breadcrumbs,
        "child_categories": child_categories,
        "page_title": page_title,
    }
    return render(request, "storefront/product_list.html", context)


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
