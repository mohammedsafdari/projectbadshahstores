"""Cart quantity rules, with no database access (D42, D43, D55, D60).

A cart quantity must be a positive multiple of the variant's own step, so a
loose item sold in 1 g steps takes 0.001 kg and a packet takes whole packs.
The rules here only look at the variant object; availability and visibility are
checked by the cart services.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from apps.catalog.models import ProductVariant

QUANTITY_PLACES = Decimal("0.001")

# Per-line caps. They are advisory: checkout re-checks under the variant lock
# (Slice 34/36). They are constants, not settings, until the owner says
# otherwise (open question, Slice 31).
MAX_PACKETS_PER_LINE = 99
MAX_LOOSE_PER_LINE = Decimal("50")


class CartError(ValueError):
    """Base error for cart rules."""


class InvalidCartQuantityError(CartError):
    """The quantity is not a valid positive multiple of the variant's step."""


class QuantityTooLargeError(CartError):
    """The quantity is above the per-line cap for this kind of variant."""


class NotPurchasableError(CartError):
    """The variant, its product, brand or category is archived."""


class InsufficientAvailabilityError(CartError):
    """The wanted quantity is more than the shop can sell right now."""

    def __init__(self, available: Decimal) -> None:
        self.available = available
        super().__init__(f"Only {available} is available.")


def _as_decimal(quantity: object) -> Decimal:
    """Accept only Decimal or int, never float, bool, str, NaN or Infinity."""
    # bool is a subclass of int, so it must be rejected first.
    if isinstance(quantity, bool):
        raise InvalidCartQuantityError("Quantity must be a Decimal or int, not a boolean.")
    if isinstance(quantity, int):
        return Decimal(quantity)
    if isinstance(quantity, Decimal):
        if not quantity.is_finite():
            raise InvalidCartQuantityError("Quantity must be a finite number.")
        return quantity
    raise InvalidCartQuantityError(
        f"Quantity must be a Decimal or int, not {type(quantity).__name__!r}."
    )


def normalise_cart_quantity(variant: ProductVariant, quantity: object) -> Decimal:
    """Validate everything except the per-line cap, and return 3 decimals.

    The merge uses this: it clamps an over-cap guest line instead of refusing
    it, but a bad type, zero, a fraction or a value off the step is still a bad
    entry and is dropped.
    """
    value = _as_decimal(quantity)

    # quantize() can overflow for an enormous exponent; that is a bad quantity,
    # not a crash.
    try:
        quantised = value.quantize(QUANTITY_PLACES)
    except InvalidOperation as exc:
        raise InvalidCartQuantityError("Quantity is too large or too precise.") from exc
    if value != quantised:
        raise InvalidCartQuantityError("Quantity must have at most 3 decimal places.")

    if quantised <= 0:
        raise InvalidCartQuantityError("Quantity must be greater than zero.")

    if not variant.is_sold_loose and quantised != quantised.to_integral_value():
        raise InvalidCartQuantityError("Packet variants accept whole numbers only.")

    step = variant.quantity_step
    if step <= 0:
        raise InvalidCartQuantityError("The variant has no valid quantity step.")
    # Decimal remainder is exact here: both values have at most 3 decimals.
    if quantised % step != 0:
        raise InvalidCartQuantityError(f"Quantity must be a multiple of {step}.")

    return quantised


def check_cart_quantity(variant: ProductVariant, quantity: object) -> Decimal:
    """Validate a quantity for one variant and return it with 3 decimals.

    Raises ``InvalidCartQuantityError`` for a bad type, zero, a negative value,
    more than three decimals, a fraction on a packet, or a value that is not a
    multiple of the variant's step. Raises ``QuantityTooLargeError`` above the
    per-line cap. The caller decides whether the result is available.
    """
    quantised = normalise_cart_quantity(variant, quantity)

    limit = MAX_LOOSE_PER_LINE if variant.is_sold_loose else Decimal(MAX_PACKETS_PER_LINE)
    if quantised > limit:
        raise QuantityTooLargeError(f"At most {limit} may be ordered at once.")

    return quantised
