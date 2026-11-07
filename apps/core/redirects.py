"""Safe redirect helpers shared by the address pages (D65).

The existing private ``_allowed_next`` copies in accounts and cart stay where
they are; new code calls this one function instead of growing another copy.
"""

from __future__ import annotations

from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme


def safe_next_url(request: HttpRequest, next_url: str | None) -> str | None:
    """Return next_url only when it points at this site, otherwise None.

    ``url_has_allowed_host_and_scheme`` accepts a path such as ``/cart/`` and
    refuses another host or a protocol-relative URL such as ``//evil.example``.
    An empty value is refused before Django is called.
    """
    if not next_url:
        return None
    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return None
