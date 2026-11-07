"""Checkout and confirmation wording: one place for every sentence (D66).

Views and templates never spell a customer-facing sentence themselves. The
``page_text`` dict holds the fixed labels; small functions build the sentences
that need a value (an amount, a day, a status).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _

from apps.cart.presentation import format_quantity
from apps.cart.services import CartLine
from apps.orders.models import Order, OrderItem

CHECKOUT_TITLE = _("Checkout")
CHECKOUT_HEADING = _("Checkout")
CONFIRMATION_TITLE = _("Order confirmed")
CONFIRMATION_HEADING = _("Thank you for your order")

ADDRESS_HEADING = _("Deliver to")
ADD_ADDRESS_LINK = _("Add an address")
NO_SERVED_ADDRESS = _(
    "We do not deliver to any of your saved addresses. Add an address we can deliver to."
)
UNSERVED_NOTE = _("We do not deliver to this pin code yet.")

CART_HEADING = _("Your order")
ITEM_TOTAL_LABEL = _("Item total")
SAVINGS_LABEL = _("You save")
DELIVERY_LABEL = _("Delivery")
FREE_LABEL = _("Free")
TOTAL_LABEL = _("Total")
DELIVERY_DAY_LABEL = _("Delivery day")

SUBSTITUTION_HEADING = _("If an item is not available")
NOTE_LABEL = _("Note for the shop (optional)")
NOTE_HELP = _("Up to 300 characters.")
PLACE_ORDER_BUTTON = _("Place order")

ORDER_NUMBER_LABEL = _("Order number")
STATUS_LABEL = _("Status")
ITEMS_HEADING = _("Items")
PAYMENT_HEADING = _("Payment")
SHIPPING_HEADING = _("Delivery address")
NOTE_HEADING = _("Your note")

EMPTY_CART_MESSAGE = _("Your cart is empty, so there is nothing to check out.")
CART_NOT_READY_MESSAGE = _(
    "Your cart is not ready for checkout. Please check the notes on your cart."
)
CART_CHANGED_MESSAGE = _(
    "Your cart changed before we could place the order. Please review it and try again."
)
NO_ADDRESS_MESSAGE = _("Add a delivery address before checking out.")
ADDRESS_NOT_SERVED_MESSAGE = _("We do not deliver to that pin code yet. Choose another address.")
ADDRESS_NOT_FOUND_MESSAGE = _("That address is not available. Choose another address.")
GROUP_INVALID_MESSAGE = _("That checkout could not be recognised. Please try again.")
GROUP_REFUSED_MESSAGE = _(
    "That checkout is no longer valid. Please review your order and try again."
)

ADDRESS_REQUIRED = _("Choose a delivery address.")
ADDRESS_INVALID = _("Choose a delivery address.")
SUBSTITUTION_INVALID = _("Choose how we should handle an item that is not available.")
NOTE_TOO_LONG = _("Keep the note to 300 characters or fewer.")

# The customer's choice when an item is out of stock (D64).
SUBSTITUTION_LABELS = {
    Order.SubstitutionPreference.SUBSTITUTE: _("Substitute a similar item"),
    Order.SubstitutionPreference.REFUND: _("Refund this item"),
    Order.SubstitutionPreference.CALL_ME: _("Call me first"),
}

# A plain sentence for every status, so the confirmation page never prints a
# database value such as "PAYMENT_PENDING".
STATUS_SENTENCES = {
    Order.Status.PLACED: _("We have received your order."),
    Order.Status.PAYMENT_PENDING: _("We are waiting for your payment."),
    Order.Status.PAID: _("We have received your payment."),
    Order.Status.CONFIRMED: _("We have confirmed your order."),
    Order.Status.PACKING: _("We are packing your order."),
    Order.Status.OUT_FOR_DELIVERY: _("Your order is out for delivery."),
    Order.Status.DELIVERED: _("Your order has been delivered."),
    Order.Status.CANCELLED: _("This order was cancelled."),
    Order.Status.PAYMENT_FAILED: _("The payment did not go through."),
    Order.Status.REFUND_PENDING: _("Your refund is being processed."),
    Order.Status.REFUNDED: _("Your refund has been paid."),
    Order.Status.PARTIALLY_REFUNDED: _("Part of your order has been refunded."),
}


def money(value: Decimal) -> str:
    """One amount with the rupee sign and two places."""
    return f"₹{value:.2f}"


def cod_sentence(grand_total: Decimal) -> str:
    """The sentence on the checkout page: cash is paid on arrival."""
    return str(
        _("Pay %(amount)s in cash when your order arrives.") % {"amount": money(grand_total)}
    )


def cod_on_delivery_sentence(amount_due: Decimal) -> str:
    """The sentence on the confirmation page: cash is paid on delivery."""
    return str(_("Pay %(amount)s in cash on delivery.") % {"amount": money(amount_due)})


def cod_limit_message(cap: Decimal) -> str:
    """Why the order cannot be placed for cash on delivery."""
    return str(
        _(
            "This order is above the %(cap)s cash-on-delivery limit. "
            "Please remove some items or call the shop."
        )
        % {"cap": money(cap)}
    )


def delivery_day_text(day: date) -> str:
    """Today, tomorrow, or the date itself, read in the shop time zone."""
    today = timezone.localdate()
    if day == today:
        return str(_("Today"))
    if day == today + timedelta(days=1):
        return str(_("Tomorrow"))
    return date_format(day, "j F Y")


def substitution_label(value: str) -> str:
    """The plain label for one substitution choice."""
    # A value read from the form is a plain str, so compare it with each
    # enum member's text instead of using a dict keyed by the enum.
    for choice, label in SUBSTITUTION_LABELS.items():
        if str(choice) == value:
            return str(label)
    return str(SUBSTITUTION_LABELS[Order.SubstitutionPreference.CALL_ME])


def status_sentence(status: str) -> str:
    """One plain sentence for the order status, or a safe fallback."""
    # The database returns a plain str, not the enum member.
    for choice, sentence in STATUS_SENTENCES.items():
        if str(choice) == status:
            return str(sentence)
    return str(_("Your order is being processed."))


def cart_quantity(line: CartLine) -> str:
    """How much of one cart line the customer is buying."""
    return format_quantity(line.variant, line.quantity)


def item_quantity(item: OrderItem) -> str:
    """How much of one frozen order line the customer bought."""
    return f"{item.quantity_ordered:g} x {item.pack_label}"


def page_text() -> dict[str, str]:
    """Every fixed sentence the checkout and confirmation pages print."""
    return {
        "checkout_title": str(CHECKOUT_TITLE),
        "checkout_heading": str(CHECKOUT_HEADING),
        "confirmation_title": str(CONFIRMATION_TITLE),
        "confirmation_heading": str(CONFIRMATION_HEADING),
        "address_heading": str(ADDRESS_HEADING),
        "add_address_link": str(ADD_ADDRESS_LINK),
        "no_served_address": str(NO_SERVED_ADDRESS),
        "unserved_note": str(UNSERVED_NOTE),
        "cart_heading": str(CART_HEADING),
        "item_total_label": str(ITEM_TOTAL_LABEL),
        "savings_label": str(SAVINGS_LABEL),
        "delivery_label": str(DELIVERY_LABEL),
        "free_label": str(FREE_LABEL),
        "total_label": str(TOTAL_LABEL),
        "delivery_day_label": str(DELIVERY_DAY_LABEL),
        "substitution_heading": str(SUBSTITUTION_HEADING),
        "note_label": str(NOTE_LABEL),
        "note_help": str(NOTE_HELP),
        "place_order_button": str(PLACE_ORDER_BUTTON),
        "order_number_label": str(ORDER_NUMBER_LABEL),
        "status_label": str(STATUS_LABEL),
        "items_heading": str(ITEMS_HEADING),
        "payment_heading": str(PAYMENT_HEADING),
        "shipping_heading": str(SHIPPING_HEADING),
        "note_heading": str(NOTE_HEADING),
    }
