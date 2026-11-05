"""Cart wording: one place for every customer-facing cart message (D61).

The views and templates never spell an error out themselves. A loose item is
described in its own unit (kg or litre); a packet is described in packs.
"""

from __future__ import annotations

from decimal import Decimal

from django.utils.translation import gettext_lazy as _

from apps.cart.rules import (
    MAX_LOOSE_PER_LINE,
    MAX_PACKETS_PER_LINE,
    CartError,
    InsufficientAvailabilityError,
    InvalidCartQuantityError,
    NotPurchasableError,
    QuantityTooLargeError,
)
from apps.catalog.models import ProductVariant

# Bad text from a form (the cart rules have not seen it yet).
INVALID_QUANTITY_INPUT = _("Enter a quantity like 1 or 1.250.")

# Shared safe wording: never says why a product or line is unavailable.
ITEM_UNAVAILABLE = _("This item is not available right now.")

# A subclass added later without a branch here still gets a safe sentence.
UNKNOWN_CART_ERROR = _("This item could not be added. Please try again.")


def unit_label(variant: ProductVariant) -> str:
    """The word a customer sees beside a quantity."""
    if variant.is_sold_loose:
        # Loose variants are KG or L by database constraint; the fallback keeps
        # a corrupt row readable instead of raising on a page view.
        return {"KG": "kg", "L": "litre"}.get(variant.unit, variant.unit.lower())
    return "pack"


def format_quantity(variant: ProductVariant, quantity: Decimal) -> str:
    """Three decimals and the unit for loose items; whole packs otherwise."""
    if variant.is_sold_loose:
        return f"{quantity:.3f} {unit_label(variant)}"
    packs = int(quantity)
    # A tiny singular/plural choice keeps the confirmation natural.
    noun = "pack" if packs == 1 else "packs"
    return f"{packs} {noun}"


def _cap(variant: ProductVariant) -> Decimal:
    if variant.is_sold_loose:
        return MAX_LOOSE_PER_LINE
    return Decimal(MAX_PACKETS_PER_LINE)


def describe_cart_error(error: CartError, variant: ProductVariant | None) -> str:
    """Turn any cart rule error into one short, safe sentence."""
    # An unknown variant has no unit or step to name, so it gets the generic
    # sentence before any branch that needs a variant.
    if variant is None:
        return str(ITEM_UNAVAILABLE)

    if isinstance(error, InsufficientAvailabilityError):
        if error.available <= 0:
            return str(_("This item is out of stock."))
        amount = format_quantity(variant, error.available)
        return str(_("Only %(amount)s is available.") % {"amount": amount})

    if isinstance(error, InvalidCartQuantityError):
        step = format_quantity(variant, variant.quantity_step)
        return str(_("Enter an amount in steps of %(step)s.") % {"step": step})

    if isinstance(error, QuantityTooLargeError):
        limit = format_quantity(variant, _cap(variant))
        return str(_("At most %(limit)s may be ordered at once.") % {"limit": limit})

    if isinstance(error, NotPurchasableError):
        return str(ITEM_UNAVAILABLE)

    return str(UNKNOWN_CART_ERROR)
