"""Customer-facing words for the storefront, kept out of the templates (D84).

Every sentence a customer reads is a gettext constant, so it can be translated
and a test can assert the exact words. ``str()`` resolves a lazy proxy at request
time, using the language active for that request.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

FEATURED_HEADING = _("Featured products")

# Visually hidden: it names the banner region for a screen reader without
# putting a heading on the page a customer would read.
OFFERS_HEADING = _("Offers")


def home_text() -> dict[str, str]:
    """The words the home page prints, resolved for the current request."""
    return {
        "featured_heading": str(FEATURED_HEADING),
        "offers_heading": str(OFFERS_HEADING),
    }
