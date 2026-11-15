"""The product name a visitor should read (D92).

A product has an English name and an optional Hindi one. The English name is the
shop's one name for the product: page titles, alt text, the meta description,
Open Graph, JSON-LD, breadcrumbs, search, order lines and invoices always use
it. Only the name printed on the page follows the visitor's language, and a
product without a Hindi name keeps its English one.
"""

from __future__ import annotations

HINDI = "hi"


def is_hindi(language_code: str | None) -> bool:
    """True for ``hi`` and for a regional code such as ``hi-IN``."""
    if not language_code:
        return False
    return language_code == HINDI or language_code.startswith(HINDI + "-")


def product_display_name(name_en: str, name_hi: str, language_code: str | None) -> str:
    """The visible name: the Hindi one for a Hindi visitor when it is filled.

    A Hindi name of only spaces counts as empty, so the customer never sees a
    blank heading or a blank cart line. ``language_code`` comes from
    ``django.utils.translation.get_language()`` at request time, so this helper
    cannot add a database query.
    """
    if is_hindi(language_code) and (name_hi or "").strip():
        return name_hi
    return name_en
