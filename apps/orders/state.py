"""The order state machine (D10, D64).

No Django imports: this module is a plain table and two small functions, so the
allowed moves can be read and tested without a database. The stored strings
match ``apps.orders.models.Order.Status``.
"""

from __future__ import annotations

PLACED = "PLACED"
PAYMENT_PENDING = "PAYMENT_PENDING"
PAID = "PAID"
CONFIRMED = "CONFIRMED"
PACKING = "PACKING"
OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
DELIVERED = "DELIVERED"
CANCELLED = "CANCELLED"
PAYMENT_FAILED = "PAYMENT_FAILED"
REFUND_PENDING = "REFUND_PENDING"
REFUNDED = "REFUNDED"
PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"

# Every status must have an entry, including the terminal ones with an empty
# tuple. A missing status would hide a reachable state from the check.
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    PLACED: (PAYMENT_PENDING, CONFIRMED, CANCELLED),
    PAYMENT_PENDING: (PAID, PAYMENT_FAILED, CANCELLED),
    PAYMENT_FAILED: (PAYMENT_PENDING, CANCELLED),
    PAID: (CONFIRMED, REFUND_PENDING),
    CONFIRMED: (PACKING, CANCELLED),
    PACKING: (OUT_FOR_DELIVERY, CANCELLED),
    OUT_FOR_DELIVERY: (DELIVERED, CANCELLED),
    REFUND_PENDING: (REFUNDED, PARTIALLY_REFUNDED),
    DELIVERED: (),
    CANCELLED: (),
    REFUNDED: (),
    PARTIALLY_REFUNDED: (),
}

# Payment and refund states exist in the table for Phase 4, but a COD order
# never moves through them (D64).
COD_ONLY_BLOCKED: tuple[str, ...] = (
    PAYMENT_PENDING,
    PAID,
    PAYMENT_FAILED,
    REFUND_PENDING,
    REFUNDED,
    PARTIALLY_REFUNDED,
)


def can_transition(from_status: str, to_status: str) -> bool:
    """True only when ``to_status`` is an allowed next step from ``from_status``."""
    return to_status in ALLOWED_TRANSITIONS.get(from_status, ())
