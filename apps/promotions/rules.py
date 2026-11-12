"""Coupon rules: does a code work for this cart, and what does it save? (D81)

One function, ``evaluate_coupon``, answers for a customer and a cart. It reads
the database but writes nothing; Slice 44b calls it at checkout inside the
order transaction with ``for_update=True`` and then writes the redemption row.

REVIEW CAREFULLY: money. Every amount is ``Decimal`` rounded by
``apps.catalog.services.round_money`` (half-up), never a float.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import cast

from django.db.models import TextChoices
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.catalog.services import round_money
from apps.orders.models import Order
from apps.orders.presentation import money
from apps.promotions.models import CODE_PATTERN, Coupon, CouponRedemption, normalise_code

ZERO = Decimal("0.00")
HUNDRED = Decimal("100")

# A customer is "new" when they have no order outside these statuses: a
# cancelled, failed or refunded order does not count as a first order (D81).
NOT_COUNTING_STATUSES = frozenset(
    {
        Order.Status.CANCELLED,
        Order.Status.PAYMENT_FAILED,
        Order.Status.REFUND_PENDING,
        Order.Status.REFUNDED,
    }
)


class CouponRefusal(TextChoices):
    """Why a code was refused. The value is empty on a code that works."""

    LOGIN_REQUIRED = "LOGIN_REQUIRED", "Sign in required"
    NOT_FOUND = "NOT_FOUND", "No such code"
    INACTIVE = "INACTIVE", "Switched off"
    NOT_STARTED = "NOT_STARTED", "Not started"
    EXPIRED = "EXPIRED", "Expired"
    BELOW_MINIMUM = "BELOW_MINIMUM", "Below the minimum order"
    NOT_FIRST_ORDER = "NOT_FIRST_ORDER", "Not a first order"
    TOTAL_LIMIT_REACHED = "TOTAL_LIMIT_REACHED", "Total limit reached"
    PER_CUSTOMER_LIMIT_REACHED = "PER_CUSTOMER_LIMIT_REACHED", "Customer limit reached"
    NO_BENEFIT = "NO_BENEFIT", "No benefit"


# The one customer sentence per reason (D81). The minimum-order sentence needs
# the amount still short, so it is a pattern that ``%`` fills in.
MESSAGES: dict[str, object] = {
    CouponRefusal.LOGIN_REQUIRED: _("Please sign in to use a coupon."),
    CouponRefusal.NOT_FOUND: _("This coupon code is not valid."),
    CouponRefusal.INACTIVE: _("This coupon is not available right now."),
    CouponRefusal.NOT_STARTED: _("This coupon is not active yet."),
    CouponRefusal.EXPIRED: _("This coupon has expired."),
    CouponRefusal.BELOW_MINIMUM: _("Add items worth %(amount)s more to use this coupon."),
    CouponRefusal.NOT_FIRST_ORDER: _("This coupon is only for your first order."),
    CouponRefusal.TOTAL_LIMIT_REACHED: _("This coupon has been fully used."),
    CouponRefusal.PER_CUSTOMER_LIMIT_REACHED: _("You have already used this coupon."),
    CouponRefusal.NO_BENEFIT: _("This coupon gives no discount on this order."),
}


@dataclass(frozen=True)
class CouponResult:
    """What one evaluation found: the discounts when it works, else the reason."""

    ok: bool
    coupon_id: int | None
    code: str
    reason: str
    message: str
    item_discount: Decimal
    delivery_discount: Decimal


def _refusal(
    reason: str,
    *,
    coupon: Coupon | None = None,
    code: str = "",
    **params: object,
) -> CouponResult:
    """Build a refused result with its customer sentence filled in."""
    template = str(MESSAGES[reason])
    message = template % params if params else template
    return CouponResult(
        ok=False,
        coupon_id=coupon.pk if coupon is not None else None,
        code=coupon.code if coupon is not None else code,
        reason=reason,
        message=message,
        item_discount=ZERO,
        delivery_discount=ZERO,
    )


def _is_signed_in(user: object) -> bool:
    """True for a real, signed-in account; an anonymous visitor is not."""
    # ``is_authenticated`` is a property on every Django user object, and an
    # ``AnonymousUser`` is not a ``User`` here, so both checks are needed.
    return isinstance(user, User) and user.is_authenticated


def is_first_order_customer(user: User) -> bool:
    """True when the customer has no order outside ``NOT_COUNTING_STATUSES``.

    A delivered order, a partly refunded order and every in-progress status
    make the customer not new (D81).
    """
    return not Order.objects.filter(user=user).exclude(status__in=NOT_COUNTING_STATUSES).exists()


def _benefit(coupon: Coupon, item_total: Decimal, delivery_fee: Decimal) -> tuple[Decimal, Decimal]:
    """The (item discount, delivery discount) of a coupon for these totals.

    A coupon never discounts more than the items themselves, so a fixed amount
    above the item total is cut to it. Only ``FREE_DELIVERY`` touches the fee.
    """
    if coupon.kind == Coupon.Kind.FREE_DELIVERY:
        return ZERO, round_money(delivery_fee)

    if coupon.kind == Coupon.Kind.PERCENT:
        percent = coupon.percent_off if coupon.percent_off is not None else ZERO
        discount = round_money(item_total * percent / HUNDRED)
        if coupon.max_discount is not None and discount > coupon.max_discount:
            discount = coupon.max_discount
    else:
        discount = coupon.amount_off if coupon.amount_off is not None else ZERO

    discount = round_money(discount)
    if discount > item_total:
        discount = item_total
    return discount, ZERO


def _live_redemptions(coupon: Coupon):
    """Redemptions that still count against a limit (a released row does not)."""
    return CouponRedemption.objects.filter(coupon=coupon, released_at__isnull=True)


def evaluate_coupon(
    raw_code: str,
    *,
    user: object,
    item_total: Decimal,
    delivery_fee: Decimal,
    now: datetime | None = None,
    for_update: bool = False,
) -> CouponResult:
    """Judge one code for one cart, in the documented order of checks (D81).

    The first failure wins. ``for_update=True`` locks the coupon row, which
    serialises two checkouts of the same coupon so neither can pass the limit
    checks at the same time; it must therefore be called inside a transaction.
    """
    moment = now if now is not None else timezone.now()
    item_total = round_money(item_total)
    delivery_fee = round_money(delivery_fee)

    if not _is_signed_in(user):
        return _refusal(CouponRefusal.LOGIN_REQUIRED)
    # The check above proved this at run time; ``cast`` tells mypy the type.
    customer = cast(User, user)

    code = normalise_code(raw_code)
    if not CODE_PATTERN.match(code):
        return _refusal(CouponRefusal.NOT_FOUND, code=code)

    # ``select_for_update()`` locks the row(s) this query returns until the
    # surrounding transaction ends (D16).
    coupons = Coupon.objects.all()
    if for_update:
        coupons = coupons.select_for_update()
    # ``first()`` reads one row and returns None instead of raising.
    coupon = coupons.filter(code=code).first()
    if coupon is None:
        return _refusal(CouponRefusal.NOT_FOUND, code=code)

    if not coupon.is_active:
        return _refusal(CouponRefusal.INACTIVE, coupon=coupon)

    if coupon.starts_at is not None and moment < coupon.starts_at:
        return _refusal(CouponRefusal.NOT_STARTED, coupon=coupon)

    if coupon.ends_at is not None and moment >= coupon.ends_at:
        return _refusal(CouponRefusal.EXPIRED, coupon=coupon)

    # The minimum is checked on the items before any discount; reaching it
    # exactly passes.
    if item_total < coupon.min_order_value:
        short = round_money(coupon.min_order_value - item_total)
        return _refusal(CouponRefusal.BELOW_MINIMUM, coupon=coupon, amount=money(short))

    if coupon.first_order_only and not is_first_order_customer(customer):
        return _refusal(CouponRefusal.NOT_FIRST_ORDER, coupon=coupon)

    if coupon.total_limit is not None and _live_redemptions(coupon).count() >= coupon.total_limit:
        return _refusal(CouponRefusal.TOTAL_LIMIT_REACHED, coupon=coupon)

    if coupon.per_customer_limit is not None:
        used = _live_redemptions(coupon).filter(user=customer).count()
        if used >= coupon.per_customer_limit:
            return _refusal(CouponRefusal.PER_CUSTOMER_LIMIT_REACHED, coupon=coupon)

    item_discount, delivery_discount = _benefit(coupon, item_total, delivery_fee)
    if item_discount <= ZERO and delivery_discount <= ZERO:
        return _refusal(CouponRefusal.NO_BENEFIT, coupon=coupon)

    return CouponResult(
        ok=True,
        coupon_id=coupon.pk,
        code=coupon.code,
        reason="",
        message="",
        item_discount=item_discount,
        delivery_discount=delivery_discount,
    )
