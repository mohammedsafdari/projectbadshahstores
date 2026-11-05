"""Accounts app URL configuration (D59)."""

from django.urls import path

from apps.accounts import views

urlpatterns = [
    path("login/", views.login_view, name="account_login"),
    path("verify/", views.verify_view, name="account_verify"),
    path("logout/", views.logout_view, name="account_logout"),
]
