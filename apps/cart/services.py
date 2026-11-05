"""Cart business logic: quantities, live totals and the login merge (D60).

All functions take a store first (``DbCartStore`` for a customer,
``SessionCartStore`` for a guest), so the same rules serve both. Prices are
read live; nothing is snapshotted until the order (Slice 35).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from django.db import transaction

from apps.accounts.models import User
from apps.cart.models import Cart, CartItem
from apps.cart.rules import (
    MAX_LOOSE_PER_LINE,
    MAX_PACKETS_PER_LINE,
    CartError,
    InsufficientAvailabilityError,
    NotPurchasableError,
    check_cart_quantity,
    normalise_cart_quantity,
)
from apps.cart.stores import CartStore, StoredLine
from apps.catalog.models import ProductVariant
from apps.catalog.purchasable import purchasable_variant_ids
from apps.catalog.services import round_money
from apps.core.services import get_shop_settings
from apps.inventory.availability import get_available_quantities, get_available_quantity

QUANTITY_PLACES = Decimal("0.001")

# Line statuses, checked in this order (D60).
STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"
STATUS_BAD_QUANTITY = "bad_quantity"
STATUS_OUT_OF_STOCK = "out_of_stock"
STATUS_NOT_ENOUGH_STOCK = "not_enough_stock"


def _ensure_purchasable(variant: ProductVariant) -> None:
    """Refuse a variant the storefront would not show."""
    if variant.pk not in purchasable_variant_ids([variant.pk]):
        raise NotPurchasableError("This product is not for sale.")


def _is_zero(quantity: object) -> bool:
    """True only for the number zero, not for False, 0.0 or "0"."""
    if isinstance(quantity, bool):
        return False
    if isinstance(quantity, int):
        return quantity == 0
    if isinstance(quantity, Decimal):
        return quantity.is_finite() and quantity == 0
    return False


# ---------------------------------------------------------------------------
# Customer actions
# ---------------------------------------------------------------------------
def add_item(store: CartStore, variant: ProductVariant, quantity: object) -> None:
    """Add to the line, refusing a bad, too-large or unavailable amount."""
    added = check_cart_quantity(variant, quantity)
    _ensure_purchasable(variant)
    available = get_available_quantity(variant)

    def apply(items: dict[int, Decimal]) -> None:
        current = items.get(variant.pk, Decimal("0"))
        # The sum must obey the step and the per-line cap too, not only the
        # amount just added.
        combined = check_cart_quantity(variant, current + added)
        if combined > available:
            raise InsufficientAvailabilityError(available)
        items[variant.pk] = combined

    store.change(apply)


def set_item_quantity(store: CartStore, variant: ProductVariant, quantity: object) -> None:
    """Replace the line; zero removes it, and a lower amount always works."""
    if _is_zero(quantity):
        remove_item(store, variant)
        return

    wanted = check_cart_quantity(variant, quantity)
    _ensure_purchasable(variant)
    available = get_available_quantity(variant)

    def apply(items: dict[int, Decimal]) -> None:
        if wanted > available:
            raise InsufficientAvailabilityError(available)
        items[variant.pk] = wanted

    store.change(apply)


def remove_item(store: CartStore, variant: ProductVariant) -> None:
    """Remove the line if it exists. Removing a missing line does nothing."""

    def apply(items: dict[int, Decimal]) -> None:
        items.pop(variant.pk, None)

    store.change(apply)


def clear_cart(store: CartStore) -> None:
    """Remove every line, keeping a saved cart row itself."""

    def apply(items: dict[int, Decimal]) -> None:
        items.clear()

    store.change(apply)


# ---------------------------------------------------------------------------
# Live totals
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CartLine:
    """One line with its live price and its status."""

    variant: ProductVariant
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal
    mrp_total: Decimal
    savings: Decimal
    status: str
    available: Decimal


@dataclass(frozen=True)
class CartSummary:
    """Every number a cart page or checkout needs (wording is Slice 32)."""

    lines: list[CartLine]
    item_total: Decimal
    delivery_fee: Decimal
    grand_total: Decimal
    savings_total: Decimal
    minimum_order_value: Decimal
    minimum_order_met: bool
    amount_short_of_minimum: Decimal
    free_delivery_threshold: Decimal
    free_delivery_applies: bool
    amount_to_free_delivery: Decimal
    has_problem_lines: bool
    checkout_ready: bool


def _line_status(
    variant: ProductVariant,
    quantity: Decimal,
    available: Decimal,
    purchasable_ids: set[int],
) -> str:
    """Decide a line's status, in the documented order (D60)."""
    if variant.pk not in purchasable_ids:
        return STATUS_UNAVAILABLE
    try:
        check_cart_quantity(variant, quantity)
    except CartError:
        return STATUS_BAD_QUANTITY
    if available <= 0:
        return STATUS_OUT_OF_STOCK
    if quantity > available:
        return STATUS_NOT_ENOUGH_STOCK
    return STATUS_OK


def build_cart(store: CartStore) -> CartSummary:
    """Build the cart with live prices and totals.

    The query count does not grow with the number of lines: one read of the
    lines, one availability call, one purchasable call and the cached settings.
    """
    stored_lines: list[StoredLine] = store.lines()
    variant_ids = [line.variant.pk for line in stored_lines]

    available_by_id = get_available_quantities(variant_ids)
    purchasable_ids = purchasable_variant_ids(variant_ids)
    settings = get_shop_settings()

    lines: list[CartLine] = []
    for stored in stored_lines:
        variant = stored.variant
        quantity = stored.quantity
        available = available_by_id.get(variant.pk, Decimal("0"))
        status = _line_status(variant, quantity, available, purchasable_ids)
        unit_price = variant.selling_price
        line_total = round_money(unit_price * quantity)
        mrp_total = round_money(variant.mrp * quantity)
        lines.append(
            CartLine(
                variant=variant,
                quantity=quantity,
                unit_price=unit_price,
                line_total=line_total,
                mrp_total=mrp_total,
                savings=round_money(mrp_total - line_total),
                status=status,
                available=available,
            )
        )

    ok_lines = [line for line in lines if line.status == STATUS_OK]
    item_total = round_money(sum((line.line_total for line in ok_lines), Decimal("0.00")))
    savings_total = round_money(sum((line.savings for line in ok_lines), Decimal("0.00")))

    # "Empty" means nothing payable: a cart with only problem lines behaves
    # like an empty one for the fee.
    has_payable_line = bool(ok_lines)
    threshold = settings.free_delivery_threshold
    free_delivery_applies = has_payable_line and item_total >= threshold
    if not has_payable_line or free_delivery_applies:
        delivery_fee = Decimal("0.00")
    else:
        delivery_fee = round_money(settings.delivery_fee)

    grand_total = round_money(item_total + delivery_fee)

    minimum_order_met = item_total >= settings.minimum_order_value
    amount_short_of_minimum = round_money(
        max(Decimal("0.00"), settings.minimum_order_value - item_total)
    )
    amount_to_free_delivery = round_money(max(Decimal("0.00"), threshold - item_total))

    has_problem_lines = any(line.status != STATUS_OK for line in lines)
    checkout_ready = has_payable_line and not has_problem_lines and minimum_order_met

    return CartSummary(
        lines=lines,
        item_total=item_total,
        delivery_fee=delivery_fee,
        grand_total=grand_total,
        savings_total=savings_total,
        minimum_order_value=settings.minimum_order_value,
        minimum_order_met=minimum_order_met,
        amount_short_of_minimum=amount_short_of_minimum,
        free_delivery_threshold=threshold,
        free_delivery_applies=free_delivery_applies,
        amount_to_free_delivery=amount_to_free_delivery,
        has_problem_lines=has_problem_lines,
        checkout_ready=checkout_ready,
    )


# ---------------------------------------------------------------------------
# Merge on login
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MergeResult:
    """What the merge did, by variant id, so the view can show one notice."""

    merged: list[int]
    adjusted: list[int]
    dropped: list[int]

    @property
    def needs_notice(self) -> bool:
        # A property lets the view write ``result.needs_notice``, like a field.
        return bool(self.adjusted or self.dropped)


def _round_down_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Round a quantity down to a whole number of steps (merge only)."""
    steps = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return (steps * step).quantize(QUANTITY_PLACES)


def merge_guest_cart(user: User, guest_items: dict[int, Decimal]) -> MergeResult:
    """Move the guest cart into the saved cart, clamping instead of refusing.

    The headings and the saved cart row are each looked at once. The guest dict
    is copied and never changed. An empty guest cart creates no cart row.
    """
    # A shallow copy is enough: the values are immutable Decimals.
    guest = dict(guest_items)
    if not guest:
        return MergeResult(merged=[], adjusted=[], dropped=[])

    merged: list[int] = []
    adjusted: list[int] = []
    dropped: list[int] = []

    with transaction.atomic():
        cart, _created = Cart.objects.get_or_create(user=user)
        cart = Cart.objects.select_for_update().get(pk=cart.pk)

        existing = {item.variant_id: item for item in cart.items.all()}
        saved = {variant_id: item.quantity for variant_id, item in existing.items()}

        variant_ids = list(guest)
        variants = {
            variant.pk: variant for variant in ProductVariant.objects.filter(pk__in=variant_ids)
        }
        available_by_id = get_available_quantities(variant_ids)
        purchasable_ids = purchasable_variant_ids(variant_ids)

        writes: dict[int, Decimal] = {}
        for variant_id, guest_quantity in guest.items():
            variant = variants.get(variant_id)
            if variant is None or variant_id not in purchasable_ids:
                dropped.append(variant_id)
                continue
            try:
                # The cap is deliberately not applied here: a guest line above
                # it is clamped below, not dropped.
                added = normalise_cart_quantity(variant, guest_quantity)
            except CartError:
                dropped.append(variant_id)
                continue

            available = available_by_id.get(variant_id, Decimal("0"))
            if available <= 0:
                dropped.append(variant_id)
                continue

            combined = saved.get(variant_id, Decimal("0")) + added
            line_cap = (
                MAX_LOOSE_PER_LINE if variant.is_sold_loose else Decimal(MAX_PACKETS_PER_LINE)
            )
            limit = min(line_cap, available)
            if combined > limit:
                target = limit
                adjusted.append(variant_id)
            else:
                target = combined
                merged.append(variant_id)

            target = _round_down_to_step(target, variant.quantity_step)
            if target <= 0:
                dropped.append(variant_id)
                if variant_id in merged:
                    merged.remove(variant_id)
                if variant_id in adjusted:
                    adjusted.remove(variant_id)
                continue
            if target != combined:
                if variant_id in merged:
                    merged.remove(variant_id)
                if variant_id not in adjusted:
                    adjusted.append(variant_id)
            writes[variant_id] = target

        for variant_id, quantity in writes.items():
            item = existing.get(variant_id)
            if item is None:
                CartItem.objects.create(
                    cart=cart,
                    variant_id=variant_id,
                    quantity=quantity,
                )
            elif item.quantity != quantity:
                item.quantity = quantity
                item.save(update_fields=["quantity", "updated_at"])
        if writes:
            cart.save(update_fields=["updated_at"])

    return MergeResult(merged=merged, adjusted=adjusted, dropped=dropped)
