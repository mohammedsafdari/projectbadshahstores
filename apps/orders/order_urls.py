"""Customer order pages URL configuration (D70)."""

from django.urls import path

from apps.orders import views

urlpatterns = [
    path("", views.order_list, name="order_list"),
]
