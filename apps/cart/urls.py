"""Cart URL configuration (D61)."""

from django.urls import path

from apps.cart import views

urlpatterns = [
    path("", views.cart_detail, name="cart_detail"),
    path("add/", views.cart_add, name="cart_add"),
    path("update/", views.cart_update, name="cart_update"),
    path("remove/", views.cart_remove, name="cart_remove"),
]
