"""Storefront app URL configuration."""

from django.urls import path

from apps.storefront.views import (
    HomeView,
    brand_detail,
    category_detail,
    product_detail,
    product_list,
    product_search,
    search_suggest,
)

urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path("products/", product_list, name="product_list"),
    path("search/", product_search, name="product_search"),
    path("search/suggest/", search_suggest, name="search_suggest"),
    path("category/<slug:slug>/", category_detail, name="category_detail"),
    path("brand/<slug:slug>/", brand_detail, name="brand_detail"),
    path("product/<slug:slug>/", product_detail, name="product_detail"),
]
