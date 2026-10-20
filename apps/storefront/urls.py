"""Storefront app URL configuration."""

from django.urls import path

from apps.storefront.views import HomeView

urlpatterns = [
    path("", HomeView.as_view(), name="home"),
]
