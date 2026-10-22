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
    path("", include("apps.storefront.urls")),
    path("", include("apps.core.urls")),
    # Dev only: serve uploaded files from MEDIA_ROOT. static() returns an empty
    # list when DEBUG is False, so production is unaffected. The * unpacks that
    # list into urlpatterns.
    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),
]
