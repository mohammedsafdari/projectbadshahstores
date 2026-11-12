"""Promotions app models: coupons and their redemptions (D81).

A coupon is data, not code: staff create and switch off coupons in the admin.
The discount rules live in ``apps/promotions/rules.py`` (Slice 44a); the
checkout wiring is Slice 44b.

REVIEW CAREFULLY: money. Every amount is ``Decimal`` with two places, never a
float (D3).
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Upper
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

# A code is 4 to 20 characters from A-Z, 0-9 and the hyphen, stored in capitals.
CODE_PATTERN = re.compile(r"^[A-Z0-9-]{4,20}$")
CODE_MIN_LENGTH = 4
CODE_MAX_LENGTH = 20

# Money is never a float (D3).
ZERO = Decimal("0.00")


def normalise_code(raw_code: str | None) -> str:
    """Trim and upper-case a code, so `` diwali10 `` and ``DIWALI10`` match."""
    if raw_code is None:
        return ""
    return raw_code.strip().upper()


class Coupon(TimeStampedModel):
    """One discount code (D81).

    A coupon discounts the item total only. Only ``FREE_DELIVERY`` touches the
    delivery fee. One coupon per order, and the rows are never deleted: staff
    switch ``is_active`` off, because a redemption points at its coupon with
    ``PROTECT``.
    """

    class Kind(models.TextChoices):
        PERCENT = "PERCENT", "Percent off"
        FIXED = "FIXED", "Amount off"
        FREE_DELIVERY = "FREE_DELIVERY", "Free delivery"

    code = models.CharField(max_length=CODE_MAX_LENGTH, unique=True)
    # Shown to staff only; never printed to a customer.
    description = models.CharField(max_length=200, blank=True, default="")
    kind = models.CharField(max_length=20, choices=Kind.choices)

    # Which of these three a coupon uses depends on its kind (see the
    # coupon_kind_fields_match constraint below).
    percent_off = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    max_discount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    amount_off = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # The minimum is checked on the item total BEFORE the discount (D81).
    min_order_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        help_text="The item total must reach this before the coupon may be used.",
    )
    # An empty total cap means no overall limit; an empty per-customer limit
    # means unlimited uses by one customer. The default is one use.
    total_limit = models.PositiveIntegerField(null=True, blank=True)
    per_customer_limit = models.PositiveSmallIntegerField(null=True, blank=True, default=1)

    starts_at = models.DateTimeField(null=True, blank=True)
    # A missing end never expires the coupon.
    ends_at = models.DateTimeField(null=True, blank=True)

    # A first-order coupon is for a customer with no order outside
    # ``rules.NOT_COUNTING_STATUSES`` (D81).
    first_order_only = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            # A percent is a fraction of 100, so 0 and 100.01 are both refused.
            models.CheckConstraint(
                condition=(
                    models.Q(percent_off__isnull=True)
                    | models.Q(percent_off__gt=0, percent_off__lte=100)
                ),
                name="coupon_percent_off_range",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_off__isnull=True) | models.Q(amount_off__gt=0),
                name="coupon_amount_off_gt_0",
            ),
            models.CheckConstraint(
                condition=models.Q(max_discount__isnull=True) | models.Q(max_discount__gt=0),
                name="coupon_max_discount_gt_0",
            ),
            models.CheckConstraint(
                condition=models.Q(min_order_value__gte=0),
                name="coupon_min_order_value_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(total_limit__isnull=True) | models.Q(total_limit__gte=1),
                name="coupon_total_limit_gte_1",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(per_customer_limit__isnull=True) | models.Q(per_customer_limit__gte=1)
                ),
                name="coupon_per_customer_limit_gte_1",
            ),
            # Both times are optional; when both are set the end must be later.
            models.CheckConstraint(
                condition=(
                    models.Q(starts_at__isnull=True)
                    | models.Q(ends_at__isnull=True)
                    | models.Q(ends_at__gt=models.F("starts_at"))
                ),
                name="coupon_ends_after_starts",
            ),
            # Each kind may carry only its own fields: the database refuses a
            # PERCENT coupon without a percent, and so on.
            models.CheckConstraint(
                condition=(
                    models.Q(kind="PERCENT", percent_off__isnull=False, amount_off__isnull=True)
                    | models.Q(
                        kind="FIXED",
                        amount_off__isnull=False,
                        percent_off__isnull=True,
                        max_discount__isnull=True,
                    )
                    | models.Q(
                        kind="FREE_DELIVERY",
                        percent_off__isnull=True,
                        amount_off__isnull=True,
                        max_discount__isnull=True,
                    )
                ),
                name="coupon_kind_fields_match",
            ),
            # The code is always stored in capitals, whatever wrote the row.
            models.CheckConstraint(
                condition=models.Q(code=Upper(models.F("code"))),
                name="coupon_code_is_upper",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} ({self.get_kind_display()})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Store the code trimmed and in capitals, whatever the caller sent.

        This is normalisation, not a business rule: it makes the one stored
        form of a code match the one the rules look up.
        """
        self.code = normalise_code(self.code)
        super().save(*args, **kwargs)

    def clean(self) -> None:
        """Refuse a bad code, a wrong field for the kind, or a bad date range.

        Raising ``ValidationError`` with a field key makes the admin show the
        message beside that field. The database repeats these rules as check
        constraints so a direct ``save()`` cannot slip past them.
        """
        super().clean()
        # A code is case-insensitive, so tidy it before judging its format:
        # a staff member may type "diwali10" and it is stored as "DIWALI10".
        self.code = normalise_code(self.code)
        if not CODE_PATTERN.match(self.code or ""):
            raise ValidationError({"code": _("Use 4 to 20 characters: A-Z, 0-9 and the hyphen.")})
        self._clean_kind_fields()
        if (
            self.starts_at is not None
            and self.ends_at is not None
            and self.ends_at <= self.starts_at
        ):
            raise ValidationError({"ends_at": _("The end must be after the start.")})

    def _clean_kind_fields(self) -> None:
        """Refuse a field the chosen kind does not use, or a missing one it needs."""
        if self.kind == self.Kind.PERCENT:
            if self.percent_off is None:
                raise ValidationError(
                    {"percent_off": _("A percentage coupon needs a percent off.")}
                )
            if self.amount_off is not None:
                raise ValidationError({"amount_off": _("A percentage coupon takes no amount off.")})
            return

        if self.kind == self.Kind.FIXED:
            if self.amount_off is None:
                raise ValidationError({"amount_off": _("An amount coupon needs an amount off.")})
            if self.percent_off is not None:
                raise ValidationError({"percent_off": _("An amount coupon takes no percent off.")})
            if self.max_discount is not None:
                raise ValidationError(
                    {"max_discount": _("An amount coupon has no maximum discount.")}
                )
            return

        if self.kind == self.Kind.FREE_DELIVERY:
            if self.percent_off is not None:
                raise ValidationError(
                    {"percent_off": _("A free-delivery coupon takes no percent off.")}
                )
            if self.amount_off is not None:
                raise ValidationError(
                    {"amount_off": _("A free-delivery coupon takes no amount off.")}
                )
            if self.max_discount is not None:
                raise ValidationError(
                    {"max_discount": _("A free-delivery coupon has no maximum discount.")}
                )


class CouponRedemption(models.Model):
    """One use of one coupon on one order (D16, D81).

    Nothing writes these rows yet: Slice 44b creates one at checkout and sets
    ``released_at`` when an order is cancelled before send-out or its payment
    fails. A released row counts against no limit.
    """

    coupon = models.ForeignKey(
        Coupon,
        on_delete=models.PROTECT,
        related_name="redemptions",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.PROTECT,
        related_name="coupon_redemptions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="coupon_redemptions",
    )
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    delivery_discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    released_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            # One coupon may be used once on one order (D16).
            models.UniqueConstraint(
                fields=["coupon", "order"],
                name="couponredemption_unique_coupon_order",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount__gte=0),
                name="couponredemption_discount_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(delivery_discount_amount__gte=0),
                name="couponredemption_delivery_discount_gte_0",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.coupon.code} on order {self.order_id}"
