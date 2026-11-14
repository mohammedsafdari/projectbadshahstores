"""Storefront app views (Slice 23d, refactored in 23f)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.views.decorators.cache import cache_control, never_cache
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from apps.catalog.models import Brand, Category, Product
from apps.catalog.search import get_search_backend, normalise_query
from apps.core.services import get_shop_settings
from apps.delivery.services import served_pincodes
from apps.promotions.home import featured_product_ids, visible_banners
from apps.storefront import seo
from apps.storefront.filter_options import brand_options
from apps.storefront.filters import parse_filters
from apps.storefront.presentation import (
    home_text,
    home_title,
    listing_description,
    shop_display_name,
)
from apps.storefront.product_detail import get_product_detail
from apps.storefront.search_log import record_search
from apps.storefront.services import (
    CategoryTree,
    ProductCard,
    build_cards,
    visible_products,
)


def cut_meta(text: str, limit: int = 160) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    # No word break, so leave room for the ellipsis inside the limit.
    cut = cut[:space] if space > 0 else text[: limit - 1]
    return cut + "\u2026"


SORT_OPTIONS = [
    ("name", "Name"),
    ("price_asc", "Price: low to high"),
    ("price_desc", "Price: high to low"),
    ("newest", "Newest"),
]

# Search results default to relevance; the other listings must not offer it.
SEARCH_SORT_OPTIONS = [("relevance", "Relevance"), *SORT_OPTIONS]

PRODUCTS_PER_PAGE = 24

# The suggestion box asks the backend for more ids than it shows, so a few
# hidden products can drop out and the list still fills up.
SUGGESTION_CANDIDATES = 50
SUGGESTION_LIMIT = 6


def _querystring_without_page(request: HttpRequest) -> str:
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()


def _normalise_sort(
    request: HttpRequest,
    *,
    options: list[tuple[str, str]] = SORT_OPTIONS,
    default: str = "name",
) -> str:
    sort = request.GET.get("sort", default)
    if sort not in {value for value, _ in options}:
        # An unknown sort always falls back to the name, on every listing.
        sort = "name"
    return sort


def _wants_partial(request: HttpRequest) -> bool:
    """True when htmx wants only the results block, not the whole page."""
    is_htmx = request.headers.get("HX-Request") == "true"
    # After the Back button htmx asks for the page again and needs all of it.
    is_history_restore = request.headers.get("HX-History-Restore-Request") == "true"
    return is_htmx and not is_history_restore


def _record_search(request: HttpRequest, query: str, result_count: int) -> None:
    """Count one search word, on the search page's first page only (D89).

    Page 2 of the same results is the same search, so counting it again would
    double the figure. The suggest endpoint never comes here.
    """
    if request.method != "GET" or not query:
        return
    # "page" is absent on page 1; a blank value means page 1 as well.
    if (request.GET.get("page") or "1") != "1":
        return
    record_search(query, result_count)


def _clear_url(
    request: HttpRequest,
    sort: str,
    *,
    query: str | None = None,
    default_sort: str = "name",
) -> str:
    """This page's address without filters; a search query and a non-default sort are kept."""
    params: dict[str, str] = {}
    if query:
        params["q"] = query
    if sort != default_sort:
        params["sort"] = sort
    if not params:
        return request.path
    return request.path + "?" + urlencode(params)


def _render_listing(
    request: HttpRequest,
    *,
    tree: CategoryTree,
    category: Category | None = None,
    brand: Brand | None = None,
    page_title: str,
    breadcrumbs: list[dict[str, str | None]],
    child_categories: list[Category],
    query: str | None = None,
) -> HttpResponse:
    is_search = query is not None
    sort_options = SEARCH_SORT_OPTIONS if is_search else SORT_OPTIONS
    default_sort = "relevance" if is_search else "name"
    sort = _normalise_sort(request, options=sort_options, default=default_sort)
    filters = parse_filters(request.GET)
    if brand is not None:
        # A brand page is already one brand, so a ?brand= in the address is ignored.
        # replace() makes a copy of the frozen dataclass with one field changed.
        filters = replace(filters, brand_slugs=())
    query_text = query or ""
    too_short = is_search and not query_text
    heading = page_title
    if is_search:
        heading = f"Search results for “{query_text}”" if query_text else "Search"
    # SEO (D87): one description, one canonical and the Open Graph tags. Any
    # query string other than "page" is a filter or a sort, so that page is
    # never indexed and points at its clean address instead.
    shop_name = get_shop_settings().shop_name
    meta_description = cut_meta(listing_description(page_title, shop_name))
    filtered = any(key != "page" for key in request.GET)
    canonical = seo.canonical_url(request)
    context: dict[str, object] = {
        "current_sort": sort,
        "sort_options": sort_options,
        "querystring": _querystring_without_page(request),
        "breadcrumbs": breadcrumbs,
        "child_categories": child_categories,
        "page_title": page_title,
        "page_heading": heading,
        "filters": filters,
        "clear_url": _clear_url(request, sort, query=query, default_sort=default_sort),
        "is_search": is_search,
        "query": query_text,
        "meta_description": meta_description,
        "canonical_url": canonical,
        # A search page keeps the plain "noindex" it has always sent (D53).
        "robots": "noindex" if is_search else ("noindex, follow" if filtered else ""),
        "og_title": page_title,
        "og_description": meta_description,
        "og_type": "website",
        "og_url": canonical,
        "og_image": "",
    }
    if category is not None:
        # The page already shows this trail, so the JSON-LD costs nothing (D87).
        context["json_ld_blocks"] = [seo.breadcrumb_json_ld(request, breadcrumbs)]
    if is_search:
        # The no-results block links to the top-level categories, and the brand
        # filter uses the same list as All products (the whole active catalogue).
        context["top_categories"] = list(
            Category.objects.filter(parent__isnull=True, is_active=True).order_by("name")
        )
    if too_short:
        # An empty or one-letter query never touches the product tables.
        context["search_prompt"] = True
    else:
        product_ids: list[int] | None = None
        if is_search:
            hits = get_search_backend().search(query_text)
            product_ids = [hit.product_id for hit in hits]
        qs = visible_products(
            tree=tree,
            category=category,
            brand=brand,
            sort=sort,
            filters=filters,
            product_ids=product_ids,
        )
        paginator = Paginator(qs, PRODUCTS_PER_PAGE)
        page_obj = paginator.get_page(request.GET.get("page"))
        cards = build_cards(page_obj.object_list)
        context.update(
            {
                "page_obj": page_obj,
                "cards": cards,
                "result_count": paginator.count,
            }
        )
        if is_search:
            # The page is rendered either way; a log failure is swallowed.
            _record_search(request, query_text, paginator.count)
    if _wants_partial(request):
        response = render(request, "storefront/_listing_results.html", context)
    else:
        if not too_short:
            # The filter panel is not part of the swapped block, so only a full
            # page needs it.
            context["show_brand_filter"] = brand is None
            context["brand_options"] = (
                brand_options(tree=tree, category=category) if brand is None else []
            )
            context["selected_brands"] = set(filters.brand_slugs)
        response = render(request, "storefront/product_list.html", context)
    patch_vary_headers(response, ["HX-Request"])
    return response


class HomeView(TemplateView):
    """The shop front page: banners, featured products, then the introduction."""

    template_name = "storefront/home.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        request = self.request
        shop = get_shop_settings()
        shop_name = shop_display_name(shop.shop_name)
        # The cut-off and the pin codes come from the shop's own data (D87).
        text = home_text(
            shop_name=shop.shop_name,
            cutoff=shop.same_day_cutoff,
            pincodes=served_pincodes(),
        )
        context["text"] = text
        banners = visible_banners()
        context["banners"] = banners
        featured_ids = featured_product_ids()
        cards: list[ProductCard] = []
        if featured_ids:
            # "relevance" with an id list keeps the staff order; visible_products
            # then removes what the shop does not show (D84), and build_cards
            # drops a product with no live variant.
            cards = build_cards(
                visible_products(
                    tree=CategoryTree(),
                    sort="relevance",
                    product_ids=featured_ids,
                )
            )
        context["featured_cards"] = cards
        canonical = seo.absolute_url(request, reverse("home"))
        og_image = ""
        if banners:
            # The first banner is the picture a link preview shows (D87).
            banner_file = banners[0].display or banners[0].image
            if banner_file:
                og_image = seo.absolute_url(request, banner_file.url)
        context.update(
            {
                "page_title": home_title(shop.shop_name),
                "meta_description": cut_meta(text["intro"]),
                "canonical_url": canonical,
                "og_title": shop_name,
                "og_description": cut_meta(text["intro"]),
                "og_type": "website",
                "og_url": canonical,
                "og_image": og_image,
                "json_ld_blocks": [
                    seo.organization_json_ld(request, shop),
                    seo.website_json_ld(request, shop_name),
                ],
            }
        )
        return context


# These two files are the same for every visitor and cost a few queries, so a
# proxy may keep them for an hour. They carry no customer data (D90).
@require_GET
@cache_control(public=True, max_age=3600)
def robots_txt(request: HttpRequest) -> HttpResponse:
    """robots.txt as a view, so the sitemap address follows the site's own URL."""
    return HttpResponse(seo.robots_text(request), content_type="text/plain")


@require_GET
@cache_control(public=True, max_age=3600)
def sitemap_xml(request: HttpRequest) -> HttpResponse:
    """Every public page as XML, through the listing pages' own rule (D50, D87)."""
    return HttpResponse(seo.sitemap_xml_text(request), content_type="application/xml")


def product_list(request: HttpRequest) -> HttpResponse:
    tree = CategoryTree()
    return _render_listing(
        request,
        tree=tree,
        page_title="All products",
        breadcrumbs=[{"label": "Home", "url": reverse("home")}],
        child_categories=[],
    )


def product_search(request: HttpRequest) -> HttpResponse:
    tree = CategoryTree()
    query = normalise_query(request.GET.get("q", ""))
    return _render_listing(
        request,
        tree=tree,
        query=query,
        page_title=f'Search: "{query}"',
        breadcrumbs=[
            {"label": "Home", "url": reverse("home")},
            {"label": "Search", "url": None},
        ],
        child_categories=[],
    )


# require_GET answers anything but GET with 405; never_cache adds no-store.
@require_GET
@never_cache
def search_suggest(request: HttpRequest) -> HttpResponse:
    """A small HTML fragment with the best few products for a half-typed word."""
    query = normalise_query(request.GET.get("q", ""))
    if not query:
        # Too short: no backend call, no product or category table, no template.
        response = HttpResponse("")
    else:
        hits = get_search_backend().search(query, limit=SUGGESTION_CANDIDATES)
        # A list comprehension keeps the ids in the backend's best-first order.
        product_ids = [hit.product_id for hit in hits]
        # The same visibility and relevance rule as the search page (D50, D53).
        # The database then cuts the list to the few the box shows.
        qs = visible_products(tree=CategoryTree(), sort="relevance", product_ids=product_ids)
        context = {"query": query, "suggestions": build_cards(qs[:SUGGESTION_LIMIT])}
        response = render(request, "storefront/_search_suggestions.html", context)
    # A suggestion fragment must never be indexed or cached.
    response["X-Robots-Tag"] = "noindex"
    return response


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
    og_image = ""
    if detail.images:
        main_image = detail.images[0]
        main_file = main_image.medium or main_image.image
        if main_file:
            og_image = seo.absolute_url(request, main_file.url)
    canonical = seo.canonical_url(request)
    context = {
        "detail": detail,
        "product": product,
        "meta_description": meta_description,
        "page_title": f"{product.name_en} — {product.brand.name} — Badshah Store",
        "breadcrumbs": breadcrumbs,
        "shop_fssai": getattr(settings, "SHOP_FSSAI_LICENCE", ""),
        "canonical_url": canonical,
        "og_title": f"{product.name_en} — {product.brand.name}",
        "og_description": meta_description,
        "og_type": "product",
        "og_url": canonical,
        "og_image": og_image,
        "json_ld_blocks": [seo.breadcrumb_json_ld(request, breadcrumbs)],
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
