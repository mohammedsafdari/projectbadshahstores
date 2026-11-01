"""Storefront app URL configuration."""

from django.urls import path

from apps.storefront.views import (
    HomeView,
    brand_detail,
    category_detail,
    product_detail,
    product_list,
)

urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path("products/", product_list, name="product_list"),
    path("category/<slug:slug>/", category_detail, name="category_detail"),
    path("brand/<slug:slug>/", brand_detail, name="brand_detail"),
    path("product/<slug:slug>/", product_detail, name="product_detail"),
]
