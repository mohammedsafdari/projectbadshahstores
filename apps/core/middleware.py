"""The shop's language middleware (D92).

Django's ``LocaleMiddleware`` reads the language from the language cookie and
from the ``Accept-Language`` header. The shop keeps that rule everywhere except
the staff and admin areas, which always stay English so the people who run the
shop work in one language whatever a visitor chose. No URL carries a language
prefix, so the sitemap, the canonical links, robots.txt and the PWA keep their
addresses.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest
from django.middleware.locale import LocaleMiddleware
from django.utils import translation

# The prefixes of badshahstore/urls.py that must stay English. The staff
# reports sit under /staff/, so this list covers them too.
STAFF_PREFIXES = ("/admin/", "/staff/")


class ShopLocaleMiddleware(LocaleMiddleware):
    """Django's language choice, except the staff and admin pages stay English."""

    def process_request(self, request: HttpRequest) -> None:
        super().process_request(request)
        if request.path_info.startswith(STAFF_PREFIXES):
            # Django has already read the cookie and the header. Putting the
            # shop's own language back means every staff page renders in
            # English, and process_response then reports that same language.
            translation.activate(settings.LANGUAGE_CODE)
            request.LANGUAGE_CODE = translation.get_language()
