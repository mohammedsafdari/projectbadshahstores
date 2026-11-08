"""
Badshah Store — URL configuration.

Minimal for now; expanded in later phases.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # The cart prefix must come before the storefront include, because that
    # include starts with the empty pattern and matches every path.
    path("cart/", include("apps.cart.urls")),
    # Checkout also starts with a fixed prefix, so it must come before the
    # storefront include for the same reason as the cart.
    path("checkout/", include("apps.orders.urls")),
    # The customer order list also starts with a fixed prefix, so it must come
    # before the storefront include for the same reason as the cart.
    path("orders/", include("apps.orders.order_urls")),
    path("delivery/", include("apps.delivery.urls")),
    path("", include("apps.storefront.urls")),
    path("", include("apps.core.urls")),
    path("account/", include("apps.accounts.urls")),
    # Dev only: serve uploaded files from MEDIA_ROOT. static() returns an empty
    # list when DEBUG is False, so production is unaffected. The * unpacks that
    # list into urlpatterns.
    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),
]
