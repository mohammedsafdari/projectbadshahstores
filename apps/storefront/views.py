"""Storefront app views."""

from django.views.generic import TemplateView


class HomeView(TemplateView):
    template_name = "storefront/home.html"
