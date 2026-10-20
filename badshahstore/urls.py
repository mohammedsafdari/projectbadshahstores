"""
Badshah Store — URL configuration.

Minimal for now; expanded in later phases.
"""

from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
