"""Storefront app URL configuration."""

from django.urls import path

from apps.storefront.pwa_views import pwa_manifest, pwa_offline, pwa_service_worker
from apps.storefront.views import (
    HomeView,
    brand_detail,
    category_detail,
    product_detail,
    product_list,
    product_search,
    robots_txt,
    search_suggest,
    sitemap_xml,
)

urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    # The two search-engine files are views, not static files, so their
    # absolute addresses follow SITE_BASE_URL (D87).
    path("robots.txt", robots_txt, name="robots_txt"),
    path("sitemap.xml", sitemap_xml, name="sitemap"),
    # The installable web app's three files sit beside robots.txt: the manifest,
    # the service worker and the offline page (D91).
    path("manifest.webmanifest", pwa_manifest, name="pwa_manifest"),
    path("sw.js", pwa_service_worker, name="pwa_service_worker"),
    path("offline/", pwa_offline, name="pwa_offline"),
    path("products/", product_list, name="product_list"),
    path("search/", product_search, name="product_search"),
    path("search/suggest/", search_suggest, name="search_suggest"),
    path("category/<slug:slug>/", category_detail, name="category_detail"),
    path("brand/<slug:slug>/", brand_detail, name="brand_detail"),
    path("product/<slug:slug>/", product_detail, name="product_detail"),
]
