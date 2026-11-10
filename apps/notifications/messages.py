"""Order message texts: SMS and email wording with no Django imports (D69).

Keeping this module free of Django means the exact wording can be tested
without a database, and the owner can read one file to register the SMS texts
with the DLT portal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# The event names the rest of the project passes around (D69).
EVENT_PLACED = "ORDER_PLACED"
EVENT_CANCELLED = "ORDER_CANCELLED"
EVENT_OUT_FOR_DELIVERY = "ORDER_OUT_FOR_DELIVERY"
EVENT_DELIVERED = "ORDER_DELIVERED"
EVENT_STAFF_NEW_ORDER = "STAFF_NEW_ORDER"
# A failed online payment that will not be retried (D77).
EVENT_PAYMENT_FAILED = "ORDER_PAYMENT_FAILED"

# The customer events. The staff event is email only.
CUSTOMER_EVENTS = (
    EVENT_PLACED,
    EVENT_CANCELLED,
    EVENT_OUT_FOR_DELIVERY,
    EVENT_DELIVERED,
    EVENT_PAYMENT_FAILED,
)

# The brand name in every text. It is a constant, not a setting, because the
# DLT templates are registered with this exact wording.
SHOP_NAME = "Badshah Stores"

# The stored value of Order.payment_method for a khata order (D64, D68). It is
# a plain string here so this module never imports Django's TextChoices.
_CREDIT = "CREDIT"

# A fixed month table keeps the date in English whatever the server locale is,
# because the DLT texts are fixed.
_MONTH_NAMES = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

# The word each customer event uses in its email subject.
_SUBJECT_WORDS = {
    EVENT_PLACED: "placed",
    EVENT_CANCELLED: "cancelled",
    EVENT_OUT_FOR_DELIVERY: "out for delivery",
    EVENT_DELIVERED: "delivered",
    EVENT_PAYMENT_FAILED: "payment failed",
}


@dataclass(frozen=True)
class OrderNotice:
    """The order facts one message needs, copied out of the Order (D69).

    ``frozen=True`` makes the object read-only, so one builder can never change
    the data another builder sees.
    """

    order_number: str
    customer_name: str
    # The stored value: "COD" or "CREDIT".
    payment_method: str
    grand_total: Decimal
    amount_due_on_delivery: Decimal
    promised_delivery_date: date
    ship_city: str
    ship_pincode: str
    ship_phone: str
    # The online part and the khata part (D77). Both default to zero so every
    # existing caller and test keeps working unchanged.
    online_amount: Decimal = Decimal("0.00")
    credit_applied: Decimal = Decimal("0.00")
    # Absolute cart link used when a failed payment sends the customer back.
    cart_url: str = ""


def _amount(value: Decimal) -> str:
    """Write an amount with exactly two decimals: Decimal("230") -> "230.00"."""
    # The format spec ".2f" always prints two digits after the point.
    return f"{value:.2f}"


def _date(value: date) -> str:
    """Write a date as the owner's texts expect it: "07 Nov 2026"."""
    return f"{value.day:02d} {_MONTH_NAMES[value.month - 1]} {value.year}"


def _is_credit(notice: OrderNotice) -> bool:
    """Whether the khata is the first money used for this order."""
    return notice.payment_method == _CREDIT


def _is_online(notice: OrderNotice) -> bool:
    """Whether the whole order is paid online (no khata part)."""
    # A split khata-and-online order keeps payment_method CREDIT, so the
    # online part is read from its amount, not from the method.
    return notice.payment_method == "ONLINE" or notice.online_amount > Decimal("0.00")


def _payment_name(notice: OrderNotice) -> str:
    """The long payment name used in emails (D68, D77)."""
    if _is_credit(notice):
        if notice.online_amount > Decimal("0.00"):
            return "Credit (khata) and online payment"
        return "Credit (khata)"
    if _is_online(notice):
        return "Paid online"
    return "Cash on delivery"


def _short_payment_name(notice: OrderNotice) -> str:
    """The short payment name used in the staff email subject (D69)."""
    if _is_credit(notice):
        return "Credit"
    if _is_online(notice):
        return "Online"
    return "Cash on delivery"


def _customer_sentence(event: str, notice: OrderNotice) -> str:
    """The one sentence customer texts and emails share, without the brand."""
    if event == EVENT_PLACED:
        if _is_credit(notice):
            if notice.online_amount > Decimal("0.00"):
                return (
                    f"Order {notice.order_number} is placed. "
                    f"Rs {_amount(notice.online_amount)} is paid online and "
                    f"Rs {_amount(notice.credit_applied)} is on your credit account. "
                    f"Delivery: {_date(notice.promised_delivery_date)}."
                )
            return (
                f"Order {notice.order_number} is placed on your credit account. "
                f"Total Rs {_amount(notice.grand_total)}. "
                f"Delivery: {_date(notice.promised_delivery_date)}."
            )
        if _is_online(notice):
            return (
                f"Order {notice.order_number} is placed. "
                f"Rs {_amount(notice.online_amount)} is paid online. "
                f"Delivery: {_date(notice.promised_delivery_date)}."
            )
        return (
            f"Order {notice.order_number} is placed. "
            f"Pay Rs {_amount(notice.amount_due_on_delivery)} on delivery. "
            f"Delivery: {_date(notice.promised_delivery_date)}."
        )
    if event == EVENT_CANCELLED:
        return f"Order {notice.order_number} is cancelled. Call us if you have questions."
    if event == EVENT_PAYMENT_FAILED:
        link = notice.cart_url or "your cart"
        return (
            f"Order {notice.order_number} was not placed because the payment did not go "
            f"through. Your cart is saved at {link}."
        )
    if event == EVENT_OUT_FOR_DELIVERY:
        # A khata order never asks for cash here: a split khata order already
        # told the customer the cash part when it was placed.
        if not _is_credit(notice) and notice.amount_due_on_delivery > Decimal("0.00"):
            return (
                f"Order {notice.order_number} is out for delivery. "
                f"Please keep Rs {_amount(notice.amount_due_on_delivery)} ready."
            )
        return f"Order {notice.order_number} is out for delivery."
    if event == EVENT_DELIVERED:
        return f"Order {notice.order_number} is delivered. Thank you for shopping with us."
    raise ValueError(f"No customer message for event {event!r}.")


def sms_text(event: str, notice: OrderNotice) -> str:
    """The exact SMS wording for one customer event (D69).

    A staff event or an unknown event raises ValueError: staff never get an SMS.
    """
    if event not in CUSTOMER_EVENTS:
        raise ValueError(f"No SMS text for event {event!r}.")
    return f"{SHOP_NAME}: {_customer_sentence(event, notice)}"


def email_subject(event: str, notice: OrderNotice) -> str:
    """The subject line for one customer or staff event (D69)."""
    if event == EVENT_STAFF_NEW_ORDER:
        return (
            f"New order {notice.order_number}: Rs {_amount(notice.grand_total)} "
            f"({_short_payment_name(notice)})"
        )
    # dict.get returns None for an unknown key instead of raising KeyError.
    word = _SUBJECT_WORDS.get(event)
    if word is None:
        raise ValueError(f"No email subject for event {event!r}.")
    return f"{SHOP_NAME}: order {notice.order_number} {word}"


def _staff_email_body(notice: OrderNotice) -> str:
    """The plain-text email the shop gets for a new order (D69)."""
    lines = [
        f"New order {notice.order_number}",
        "",
        f"Customer: {notice.customer_name}",
        f"Phone: {notice.ship_phone}",
        f"City: {notice.ship_city}",
        f"Pin code: {notice.ship_pincode}",
        f"Payment: {_payment_name(notice)}",
        f"Total: Rs {_amount(notice.grand_total)}",
        # The * unpacks a one-item list only when there is an online part, so
        # the existing cash and credit emails keep exactly their old lines.
        *(
            [f"Paid online: Rs {_amount(notice.online_amount)}"]
            if notice.online_amount > Decimal("0.00")
            else []
        ),
        f"Amount due on delivery: Rs {_amount(notice.amount_due_on_delivery)}",
        f"Delivery: {_date(notice.promised_delivery_date)}",
    ]
    # "\n".join puts one newline between the parts; the "" entries are the
    # blank lines that separate the paragraphs.
    return "\n".join(lines)


def email_body(event: str, notice: OrderNotice) -> str:
    """The plain-text email body for one customer or staff event (D69)."""
    if event == EVENT_STAFF_NEW_ORDER:
        return _staff_email_body(notice)
    if event not in CUSTOMER_EVENTS:
        raise ValueError(f"No email body for event {event!r}.")
    lines = [
        f"Hello {notice.customer_name},",
        "",
        _customer_sentence(event, notice),
        "",
        f"Order: {notice.order_number}",
        f"Total: Rs {_amount(notice.grand_total)}",
    ]
    # A failed payment must not be described as if the money had arrived.
    if event != EVENT_PAYMENT_FAILED:
        lines.append(f"Payment: {_payment_name(notice)}")
    lines += [
        f"Delivery: {_date(notice.promised_delivery_date)}",
        "",
        "Thank you,",
        SHOP_NAME,
    ]
    return "\n".join(lines)
