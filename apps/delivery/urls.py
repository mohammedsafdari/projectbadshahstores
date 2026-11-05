"""Delivery URL configuration (D62)."""

from django.urls import path

from apps.delivery import views

urlpatterns = [
    path("check/", views.delivery_check, name="delivery_check"),
]
