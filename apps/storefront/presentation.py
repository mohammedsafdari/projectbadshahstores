"""Customer-facing words for the storefront, kept out of the templates (D84).

Every sentence a customer reads is a gettext constant, so it can be translated
and a test can assert the exact words. ``str()`` resolves a lazy proxy at request
time, using the language active for that request.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import time

from django.utils.translation import gettext_lazy as _

FEATURED_HEADING = _("Featured products")

# Visually hidden: it names the banner region for a screen reader without
# putting a heading on the page a customer would read.
OFFERS_HEADING = _("Offers")

# The shop's own name, used when staff leave ShopSettings.shop_name blank.
SHOP_NAME_FALLBACK = "Badshah Store"

HOME_TITLE = _("%(shop)s — provision store in Indore")

HOME_INTRO = _("A neighbourhood provision shop in Saifee Nagar, Indore, now taking orders online.")

HOME_DELIVERY_HEADING = _("Delivery")

# The cut-off and the pin codes are filled from the shop's own settings (D87).
HOME_DELIVERY_SENTENCE = _(
    "Order before %(cutoff)s and the delivery goes out the same day. A later order arrives "
    "the next day."
)
HOME_DELIVERY_PINS = _("We deliver to pin codes %(pincodes)s.")
HOME_DELIVERY_NO_PINS = _("We deliver only inside the pin codes the shop serves.")
HOME_PIN_CHECK_LINK = _("Check your pin code")

HOME_PAYMENT_HEADING = _("Ways to pay")
HOME_PAYMENT_CASH = _("Cash on delivery, paid to the delivery person.")
HOME_PAYMENT_CREDIT = _("On your shop credit account (khata), if the shop has approved one.")
HOME_PAYMENT_ONLINE = _("Online, by card or UPI, when the shop offers it at checkout.")

# The listing pages' meta description; the name is the page's own heading.
LISTING_DESCRIPTION = _(
    "Shop %(name)s at %(shop)s. Groceries, snacks and everyday needs delivered in Indore."
)


def format_cutoff(cutoff: time) -> str:
    """A time as a customer reads it: 17:00 becomes "5 pm".

    The hour is worked out in plain Python rather than with ``strftime``,
    because a Windows clock pads the hour ("05:00 PM").
    """
    hour = cutoff.hour % 12 or 12
    suffix = "am" if cutoff.hour < 12 else "pm"
    if cutoff.minute:
        return f"{hour}:{cutoff.minute:02d} {suffix}"
    return f"{hour} {suffix}"


def shop_display_name(shop_name: str) -> str:
    """The name staff entered, or the shop's own name when it is blank."""
    return shop_name.strip() or SHOP_NAME_FALLBACK


def home_title(shop_name: str) -> str:
    """The home page's one title tag."""
    return str(HOME_TITLE) % {"shop": shop_display_name(shop_name)}


def listing_description(name: str, shop_name: str) -> str:
    """One listing page's meta description, before ``cut_meta`` shortens it."""
    return str(LISTING_DESCRIPTION) % {"name": name, "shop": shop_display_name(shop_name)}


def home_text(
    *,
    shop_name: str = "",
    cutoff: time | None = None,
    pincodes: Sequence[str] = (),
) -> dict[str, str]:
    """The words the home page prints, resolved for the current request."""
    delivery = ""
    if cutoff is not None:
        if pincodes:
            pins_sentence = str(HOME_DELIVERY_PINS) % {"pincodes": ", ".join(pincodes)}
        else:
            pins_sentence = str(HOME_DELIVERY_NO_PINS)
        cutoff_sentence = str(HOME_DELIVERY_SENTENCE) % {"cutoff": format_cutoff(cutoff)}
        delivery = f"{cutoff_sentence} {pins_sentence}"
    return {
        "shop_name": shop_display_name(shop_name),
        "intro": str(HOME_INTRO),
        "delivery_heading": str(HOME_DELIVERY_HEADING),
        "delivery": delivery,
        "pin_check_link": str(HOME_PIN_CHECK_LINK),
        "payment_heading": str(HOME_PAYMENT_HEADING),
        "payment_cash": str(HOME_PAYMENT_CASH),
        "payment_credit": str(HOME_PAYMENT_CREDIT),
        "payment_online": str(HOME_PAYMENT_ONLINE),
        "featured_heading": str(FEATURED_HEADING),
        "offers_heading": str(OFFERS_HEADING),
    }
