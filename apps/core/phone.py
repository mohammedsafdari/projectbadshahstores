"""Phone number normalisation for India.

Kept behind a single function so the implementation can be swapped for the
phonenumbers library later without touching call sites.

For now: India only. We normalise to E.164 without the leading "+".
We accept:

* a ten-digit number beginning with 6-9 (the "national significant number"),
* the same preceded by a national prefix "0",
* or an international prefix "91" or "+91",

and return a string like "+91XXXXXXXXXX" (with the leading plus).
"""

import re

# Raw input may contain spaces, dashes, dots and brackets.
_PHONE_BRACKETS = re.compile(r"[().\[\]{}\-\.\\\s]+")

# 10-digit national significant number beginning with 6-9.
_INDIA_NSN = re.compile(r"^[6-9][0-9]{9}$")

# Same 10 digits after a national prefix 0.
_INDIA_NSN_WITH_NATIONAL = re.compile(r"^0([6-9][0-9]{9})$")

# Same 10 digits after an international prefix 91 or +91.
_INDIA_NSN_WITH_INTL = re.compile(r"^(?:\+?91)?([6-9][0-9]{9})$")


def normalise_phone(raw: str) -> str:
    """Normalise an India mobile number to E.164 "+91XXXXXXXXXX".

    Raises ValueError with a clear message when the input cannot be parsed by
    this minimal India-only implementation.
    """
    if not isinstance(raw, str):
        raise ValueError(f"Expected a phone string, got {type(raw).__name__!r}")

    raw = raw.strip()
    if not raw:
        raise ValueError("Phone number must not be empty")

    cleaned = _PHONE_BRACKETS.sub("", raw)
    if not cleaned:
        raise ValueError("Phone number contained no digits after cleaning")

    # Accept "+919876543210", "919876543210", "09876543210" or "9876543210".
    m = _INDIA_NSN.match(cleaned)
    if m:
        return f"+91{cleaned}"

    m = _INDIA_NSN_WITH_NATIONAL.match(cleaned)
    if m:
        return f"+91{m.group(1)}"

    m = _INDIA_NSN_WITH_INTL.match(cleaned)
    if m:
        return f"+91{m.group(1)}"

    raise ValueError(
        "Could not parse phone as an India mobile number. "
        "Expected a 10-digit number starting with 6-9, optionally preceded by "
        "'0', '91' or '+91'."
    )
