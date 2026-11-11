"""Checkout and confirmation wording: one place for every sentence (D66).

Views and templates never spell a customer-facing sentence themselves. The
``page_text`` dict holds the fixed labels; small functions build the sentences
that need a value (an amount, a day, a status).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import quote

from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from apps.cart.presentation import format_quantity
from apps.cart.services import CartLine
from apps.core.phone import normalise_phone
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
# The two parts of a split order (D74).
FROM_CREDIT_LABEL = _("From credit")
CASH_ON_DELIVERY_LABEL = _("Cash on delivery")
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
PAYMENT_METHOD_INVALID = _("Choose cash on delivery or credit.")
REMAINDER_INVALID = _("Choose cash on delivery or pay online.")
CREDIT_UNAVAILABLE_MESSAGE = _("Credit is not available for this account.")

MY_ORDERS_TITLE = _("My orders")
MY_ORDERS_HEADING = _("My orders")
NO_ORDERS_MESSAGE = _("You have not placed an order yet.")
START_SHOPPING_LINK = _("Start shopping")
PLACED_ON_LABEL = _("Placed on")
VIEW_ORDER_LINK = _("View order")
PREVIOUS_LINK = _("Previous")
NEXT_LINK = _("Next")

ORDER_DETAIL_TITLE = _("Order details")
ORDER_DETAIL_HEADING = _("Order details")
TIMELINE_HEADING = _("Order progress")
BACK_TO_ORDERS_LINK = _("All my orders")
VIEW_THIS_ORDER_LINK = _("View this order")
CANCEL_ORDER_LINK = _("Cancel this order")
CANCEL_TITLE = _("Cancel order")
CANCEL_HEADING = _("Cancel this order?")
CANCEL_HELP = _("You can cancel until we start packing.")
CANCEL_REASON_LABEL = _("Reason (optional)")
CANCEL_CONFIRM_BUTTON = _("Yes, cancel my order")
KEEP_ORDER_LINK = _("No, keep my order")
CANCEL_REASON_TOO_LONG = _("Keep the reason to 200 characters or fewer.")
ORDER_CANCELLED_MESSAGE = _("Your order has been cancelled.")
CANCEL_NOT_POSSIBLE_MESSAGE = _("This order can no longer be cancelled. Please contact us.")
ALREADY_CANCELLED_MESSAGE = _("This order is already cancelled.")
WHATSAPP_BUTTON = _("Message us on WhatsApp")
WHATSAPP_MESSAGE = _("Hello, I have a question about my order %(order_number)s.")

# The customer's choice of how to pay (D66, D68, D77).
PAYMENT_METHOD_LABELS = {
    Order.PaymentMethod.COD: _("Cash on delivery"),
    Order.PaymentMethod.CREDIT: _("Credit (khata)"),
    Order.PaymentMethod.ONLINE: _("Online payment"),
}

# Paying the part the khata does not cover (D77).
REMAINDER_HEADING = _("How would you like to pay the rest?")
PAY_ONLINE_LABEL = _("Pay online")
ONLINE_UNAVAILABLE_MESSAGE = _(
    "Online payment is not available right now. Choose cash on delivery."
)
PAID_ONLINE_LABEL = _("Paid online")
PAY_ONLINE_SENTENCE = _("Pay %(amount)s online now.")
PAID_ONLINE_SENTENCE = _("Paid online: %(amount)s.")
PAYMENT_PENDING_HEADING = _("Complete your payment")
PAYMENT_PENDING_SENTENCE = _("We are waiting for your online payment.")
PAYMENT_FAILED_MESSAGE = _("The payment did not go through. You can try again or update your cart.")
PAY_NOW_BUTTON = _("Pay now")
TRY_AGAIN_BUTTON = _("Try again")
UPDATE_CART_LINK = _("Update cart")
# The cancel page explains the money before the customer confirms (D78).
CANCEL_REFUND_HELP = _(
    "If you paid online, we send the money back to the same payment method. "
    "Any cancellation fee we keep is shown on your order."
)
# The order page says where the money is (D78).
REFUND_SENDING_SENTENCE = _("We are sending %(amount)s back to you.")
REFUND_DONE_SENTENCE = _("We have refunded %(amount)s to you.")
REFUND_FEE_SENTENCE = _("A cancellation fee of %(fee)s was kept.")
REFUND_FAILED_SENTENCE = _("The refund could not be completed. Please contact us.")
PAYMENT_RETURN_TITLE = _("Payment")
PAYMENT_CONFIRMING_HEADING = _("Checking your payment")
PAYMENT_CONFIRMING_MESSAGE = _(
    "We are checking your payment with the gateway. This page updates by itself."
)
PAYMENT_CAPTURED_MESSAGE = _("We have received your payment.")
PAYMENT_NOT_RECEIVED_MESSAGE = _("We have not received this payment yet.")
PAYMENT_FAILED_PAGE_MESSAGE = _("This payment did not go through.")

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

# A short customer label for each step on the order progress timeline (D70).
TIMELINE_LABELS = {
    Order.Status.PLACED: _("Order placed"),
    Order.Status.PAYMENT_PENDING: _("Waiting for payment"),
    Order.Status.PAID: _("Payment received"),
    Order.Status.CONFIRMED: _("Order confirmed"),
    Order.Status.PACKING: _("Packing your order"),
    Order.Status.OUT_FOR_DELIVERY: _("Out for delivery"),
    Order.Status.DELIVERED: _("Delivered"),
    Order.Status.CANCELLED: _("Order cancelled"),
    Order.Status.PAYMENT_FAILED: _("Payment failed"),
    Order.Status.REFUND_PENDING: _("Refund in progress"),
    Order.Status.REFUNDED: _("Refunded"),
    Order.Status.PARTIALLY_REFUNDED: _("Partly refunded"),
}


def money(value: Decimal) -> str:
    """One amount with the rupee sign and two places."""
    return f"₹{value:.2f}"


def cod_sentence(grand_total: Decimal) -> str:
    """The sentence on the checkout page: cash is paid on arrival."""
    return str(
        _("Pay %(amount)s in cash when your order arrives.") % {"amount": money(grand_total)}
    )


def online_sentence(amount: Decimal) -> str:
    """The checkout sentence when the rest is paid online (D77)."""
    return str(PAY_ONLINE_SENTENCE % {"amount": money(amount)})


def paid_online_sentence(amount: Decimal) -> str:
    """The confirmation sentence for the part already paid online."""
    return str(PAID_ONLINE_SENTENCE % {"amount": money(amount)})


def online_amount_label(*, paid: bool) -> str:
    """The summary row label: "Paid online" only after the gateway confirms it."""
    return str(PAID_ONLINE_LABEL if paid else PAY_ONLINE_LABEL)


def online_unavailable_message() -> str:
    """Why the online radio cannot be used."""
    return str(ONLINE_UNAVAILABLE_MESSAGE)


def cod_on_delivery_sentence(amount_due: Decimal) -> str:
    """The sentence on the confirmation page: cash is paid on delivery."""
    return str(_("Pay %(amount)s in cash on delivery.") % {"amount": money(amount_due)})


def cod_limit_message(cap: Decimal) -> str:
    """Why the cash part cannot be paid on delivery."""
    return str(
        _(
            "The cash part of this order is above the %(cap)s cash-on-delivery limit. "
            "Please remove some items or call the shop."
        )
        % {"cap": money(cap)}
    )


def available_credit_sentence(available: Decimal) -> str:
    """How much credit the customer may still use."""
    return str(_("Available credit: %(amount)s") % {"amount": money(available)})


def credit_choice_sentence(credit_amount: Decimal, cash_due: Decimal) -> str:
    """What ticking the khata checkbox will do with this order (D74)."""
    if cash_due > Decimal("0.00"):
        return str(
            _("%(credit)s will be added to your khata balance. Pay %(cash)s in cash on delivery.")
            % {"credit": money(credit_amount), "cash": money(cash_due)}
        )
    return str(
        _("%(amount)s will be added to your khata balance. Nothing to pay on delivery.")
        % {"amount": money(credit_amount)}
    )


def credit_limit_message(available: Decimal) -> str:
    """Why credit cannot be used for this order."""
    return str(
        _("Your available credit %(amount)s is below this order.") % {"amount": money(available)}
    )


def no_credit_left_message() -> str:
    """The checkout error when the account has no credit left (D74)."""
    return str(
        _("There is no credit left on this account. Choose cash on delivery or call the shop.")
    )


def credit_refused_message(available: Decimal) -> str:
    """The checkout error when the charge is refused for the limit."""
    return str(
        _(
            "Your available credit %(amount)s is below this order. "
            "Choose cash on delivery or call the shop."
        )
        % {"amount": money(available)}
    )


def credit_confirmation_sentence(amount: Decimal) -> str:
    """The confirmation sentence for the part paid on credit."""
    return str(_("Added to your khata: %(amount)s.") % {"amount": money(amount)})


def credit_returned_sentence(amount: Decimal) -> str:
    """The detail sentence for a cancelled credit order (D70)."""
    return str(_("%(amount)s was returned to your khata.") % {"amount": money(amount)})


def refund_sentence(amount: Decimal, fee: Decimal, status: str) -> str:
    """Where the money is for a cancelled paid order (D78).

    ``status`` is a plain stored string, not the enum, because that is what the
    database returns. A failed refund asks the customer to contact the shop.
    """
    if status == "FAILED":
        return str(REFUND_FAILED_SENTENCE)
    if status == "PROCESSED":
        done = str(REFUND_DONE_SENTENCE % {"amount": money(amount)})
        if fee > Decimal("0.00"):
            # One space joins the two sentences; both end with a full stop.
            return f"{done} {REFUND_FEE_SENTENCE % {'fee': money(fee)}}"
        return done
    return str(REFUND_SENDING_SENTENCE % {"amount": money(amount)})


def payment_method_label(value: str) -> str:
    """The plain label for one payment method."""
    # A value read from the form is a plain str, so compare it with each
    # enum member's text instead of using a dict keyed by the enum.
    for choice, label in PAYMENT_METHOD_LABELS.items():
        if str(choice) == value:
            return str(label)
    return str(PAYMENT_METHOD_LABELS[Order.PaymentMethod.COD])


def delivery_day_text(day: date) -> str:
    """Today, tomorrow, or the date itself, read in the shop time zone."""
    today = timezone.localdate()
    if day == today:
        return str(_("Today"))
    if day == today + timedelta(days=1):
        return str(_("Tomorrow"))
    return date_format(day, "j F Y")


def order_date_text(moment: datetime) -> str:
    """The date an order was placed, read in the shop time zone."""
    # A stored time is UTC; 20:00 UTC on 7 Nov is already 8 Nov in India.
    local = timezone.localtime(moment)
    return date_format(local, "j M Y")


def item_count_text(count: int) -> str:
    """How many items one order holds, singular or plural."""
    return str(ngettext("%(count)d item", "%(count)d items", count) % {"count": count})


def page_label(number: int, total: int) -> str:
    """The pagination label, for example Page 2 of 5."""
    return str(_("Page %(number)d of %(total)d") % {"number": number, "total": total})


def cancel_question(order_number: str) -> str:
    """The question the cancel page asks before it changes anything."""
    return str(
        _("Are you sure you want to cancel order %(order_number)s?")
        % {"order_number": order_number}
    )


def whatsapp_href(number: str, order_number: str) -> str:
    """A plain wa.me link with the order number already in the message.

    An empty or invalid number returns an empty string, so the page can hide
    the button instead of printing a broken link.
    """
    if not number.strip():
        return ""
    try:
        # normalise_phone returns "+91XXXXXXXXXX"; wa.me wants the digits only.
        digits = normalise_phone(number).removeprefix("+")
    except ValueError:
        return ""
    message = str(WHATSAPP_MESSAGE % {"order_number": order_number})
    return f"https://wa.me/{digits}?text={quote(message, safe='')}"


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


def timeline_label(status: str) -> str:
    """The short customer label for one step on the order timeline."""
    for choice, label in TIMELINE_LABELS.items():
        if str(choice) == status:
            return str(label)
    return status_sentence(status)


def timeline_time_text(moment: datetime) -> str:
    """The date and time of one timeline step, in the shop time zone."""
    local = timezone.localtime(moment)
    return date_format(local, "j M Y, H:i")


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
        "from_credit_label": str(FROM_CREDIT_LABEL),
        "cash_on_delivery_label": str(CASH_ON_DELIVERY_LABEL),
        "delivery_day_label": str(DELIVERY_DAY_LABEL),
        "substitution_heading": str(SUBSTITUTION_HEADING),
        "note_label": str(NOTE_LABEL),
        "note_help": str(NOTE_HELP),
        "place_order_button": str(PLACE_ORDER_BUTTON),
        "order_number_label": str(ORDER_NUMBER_LABEL),
        "status_label": str(STATUS_LABEL),
        "items_heading": str(ITEMS_HEADING),
        "payment_heading": str(PAYMENT_HEADING),
        "payment_method_cod": payment_method_label(Order.PaymentMethod.COD),
        "payment_method_credit": payment_method_label(Order.PaymentMethod.CREDIT),
        "payment_method_online": payment_method_label(Order.PaymentMethod.ONLINE),
        "remainder_heading": str(REMAINDER_HEADING),
        "pay_online_label": str(PAY_ONLINE_LABEL),
        "paid_online_label": str(PAID_ONLINE_LABEL),
        "payment_pending_heading": str(PAYMENT_PENDING_HEADING),
        "payment_pending_sentence": str(PAYMENT_PENDING_SENTENCE),
        "payment_failed_message": str(PAYMENT_FAILED_MESSAGE),
        "pay_now_button": str(PAY_NOW_BUTTON),
        "try_again_button": str(TRY_AGAIN_BUTTON),
        "update_cart_link": str(UPDATE_CART_LINK),
        "cancel_refund_help": str(CANCEL_REFUND_HELP),
        "payment_return_title": str(PAYMENT_RETURN_TITLE),
        "payment_confirming_heading": str(PAYMENT_CONFIRMING_HEADING),
        "payment_confirming_message": str(PAYMENT_CONFIRMING_MESSAGE),
        "payment_captured_message": str(PAYMENT_CAPTURED_MESSAGE),
        "payment_not_received_message": str(PAYMENT_NOT_RECEIVED_MESSAGE),
        "payment_failed_page_message": str(PAYMENT_FAILED_PAGE_MESSAGE),
        "shipping_heading": str(SHIPPING_HEADING),
        "note_heading": str(NOTE_HEADING),
        "my_orders_title": str(MY_ORDERS_TITLE),
        "my_orders_heading": str(MY_ORDERS_HEADING),
        "no_orders_message": str(NO_ORDERS_MESSAGE),
        "start_shopping_link": str(START_SHOPPING_LINK),
        "placed_on_label": str(PLACED_ON_LABEL),
        "view_order_link": str(VIEW_ORDER_LINK),
        "previous_link": str(PREVIOUS_LINK),
        "next_link": str(NEXT_LINK),
        "order_detail_title": str(ORDER_DETAIL_TITLE),
        "order_detail_heading": str(ORDER_DETAIL_HEADING),
        "timeline_heading": str(TIMELINE_HEADING),
        "back_to_orders_link": str(BACK_TO_ORDERS_LINK),
        "view_this_order_link": str(VIEW_THIS_ORDER_LINK),
        "cancel_order_link": str(CANCEL_ORDER_LINK),
        "cancel_title": str(CANCEL_TITLE),
        "cancel_heading": str(CANCEL_HEADING),
        "cancel_help": str(CANCEL_HELP),
        "cancel_reason_label": str(CANCEL_REASON_LABEL),
        "cancel_confirm_button": str(CANCEL_CONFIRM_BUTTON),
        "keep_order_link": str(KEEP_ORDER_LINK),
        "whatsapp_button": str(WHATSAPP_BUTTON),
    }
