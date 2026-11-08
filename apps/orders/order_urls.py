"""Customer order pages URL configuration (D70)."""

from django.urls import path

from apps.orders import views

urlpatterns = [
    path("", views.order_list, name="order_list"),
    path("<uuid:group>/", views.order_detail, name="order_detail"),
    path("<uuid:group>/cancel/", views.order_cancel, name="order_cancel"),
]
