"""Orders URL configuration (D66)."""

from django.urls import path

from apps.orders import views

urlpatterns = [
    path("", views.checkout, name="checkout"),
    path("done/<uuid:group>/", views.order_confirmation, name="order_confirmation"),
]
