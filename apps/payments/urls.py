"""Payment routes (D77).

Order numbers contain slashes (``BS/26-27/000001``), so every route uses the
``path`` converter and the exact stored number.
"""

from django.urls import path

from apps.payments import views

urlpatterns = [
    path("start/<path:order_number>/", views.payment_start, name="payment_start"),
    path("return/<path:order_number>/", views.payment_return, name="payment_return"),
    path("status/<path:order_number>/", views.payment_status, name="payment_status"),
    path(
        "webhook/<str:provider>/",
        views.payment_webhook,
        name="payment_webhook",
    ),
]
