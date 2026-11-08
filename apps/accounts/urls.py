"""Accounts app URL configuration (D59)."""

from django.urls import path

from apps.accounts import address_views, verification_views, views

urlpatterns = [
    path("login/", views.login_view, name="account_login"),
    path("signup/", views.signup_view, name="account_signup"),
    path("verify/", views.verify_view, name="account_verify"),
    path("logout/", views.logout_view, name="account_logout"),
    path("verify-phone/", verification_views.verify_phone, name="account_verify_phone"),
    path("verify-email/", verification_views.verify_email, name="account_verify_email"),
    path("addresses/", address_views.address_list, name="address_list"),
    path("addresses/add/", address_views.address_add, name="address_add"),
    path("addresses/<int:pk>/edit/", address_views.address_edit, name="address_edit"),
    path("addresses/<int:pk>/archive/", address_views.address_archive, name="address_archive"),
    path(
        "addresses/<int:pk>/default/",
        address_views.address_set_default,
        name="address_set_default",
    ),
]
