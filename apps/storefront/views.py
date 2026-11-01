"""Storefront app views (Slice 23d)."""

from __future__ import annotations

from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.generic import TemplateView

from apps.catalog.models import Brand, Category
from apps.storefront.services import category_ancestors, listing_products


class HomeView(TemplateView):
    template_name = "storefront/home.html"


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


def product_list(request: HttpRequest) -> HttpResponse:
    sort = _normalise_sort(request)
    cards = listing_products(sort=sort)
    paginator = Paginator(cards, PRODUCTS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page"))
    context = {
        "page_obj": page_obj,
        "cards": page_obj.object_list,
        "current_sort": sort,
        "sort_options": SORT_OPTIONS,
        "querystring": _querystring_without_page(request),
        "breadcrumbs": [{"label": "Home", "url": reverse("home")}],
        "child_categories": [],
        "page_title": "All products",
    }
    return render(request, "storefront/product_list.html", context)


def category_detail(request: HttpRequest, slug: str) -> HttpResponse:
    category = get_object_or_404(Category, slug=slug, is_active=True)
    for ancestor in category_ancestors(category):
        if not ancestor.is_active:
            raise Http404
    sort = _normalise_sort(request)
    cards = listing_products(category=category, sort=sort)
    paginator = Paginator(cards, PRODUCTS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page"))
    child_categories = list(Category.objects.filter(parent=category, is_active=True))
    breadcrumbs: list[dict[str, str | None]] = [{"label": "Home", "url": reverse("home")}]
    for ancestor in category_ancestors(category):
        breadcrumbs.append(
            {
                "label": ancestor.name,
                "url": reverse("category_detail", args=[ancestor.slug]),
            }
        )
    breadcrumbs.append({"label": category.name, "url": None})
    context = {
        "page_obj": page_obj,
        "cards": page_obj.object_list,
        "current_sort": sort,
        "sort_options": SORT_OPTIONS,
        "querystring": _querystring_without_page(request),
        "breadcrumbs": breadcrumbs,
        "child_categories": child_categories,
        "page_title": category.name,
    }
    return render(request, "storefront/product_list.html", context)


def brand_detail(request: HttpRequest, slug: str) -> HttpResponse:
    brand = get_object_or_404(Brand, slug=slug, is_active=True)
    sort = _normalise_sort(request)
    cards = listing_products(brand=brand, sort=sort)
    paginator = Paginator(cards, PRODUCTS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page"))
    breadcrumbs: list[dict[str, str | None]] = [
        {"label": "Home", "url": reverse("home")},
        {"label": brand.name, "url": None},
    ]
    context = {
        "page_obj": page_obj,
        "cards": page_obj.object_list,
        "current_sort": sort,
        "sort_options": SORT_OPTIONS,
        "querystring": _querystring_without_page(request),
        "breadcrumbs": breadcrumbs,
        "child_categories": [],
        "page_title": brand.name,
    }
    return render(request, "storefront/product_list.html", context)
