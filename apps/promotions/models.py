"""Promotions app models: coupons, redemptions, banners and featured products.

A coupon, a banner and a featured product are all data, not code: staff manage
them in the admin. The coupon rules live in ``apps/promotions/rules.py``
(Slice 44a) and the home page queries in ``apps/promotions/home.py`` (D84).

REVIEW CAREFULLY: money. Every amount is ``Decimal`` with two places, never a
float (D3).
"""

from __future__ import annotations

import logging
import re
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Upper
from django.utils.translation import gettext_lazy as _

from apps.catalog.validators import (
    validate_image_content,
    validate_image_extension,
    validate_image_size,
)
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
            # The database repeats the code's format, so a direct ``update()``
            # cannot store a code the checkout would never find (D82).
            models.CheckConstraint(
                condition=models.Q(code__regex=r"^[A-Z0-9-]{4,20}$"),
                name="coupon_code_format",
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

    ``place_order`` writes one row with the order (D82). ``released_at`` is set
    when the order is cancelled BEFORE it was sent out, so the same customer may
    use the code again; a released row counts against no limit. A cancel after
    send-out keeps the use counted, and so does a ``PAYMENT_FAILED`` order: that
    status keeps its stock and may be paid again (D77).
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


# --- the home page: banners and featured products (D84) ----------------------

logger = logging.getLogger(__name__)

# The display copy of a banner: a WebP at most 1200 px on its longest side.
BANNER_DISPLAY_MAX_SIDE = 1200
BANNER_DISPLAY_QUALITY = 80

# At most this many products may be featured on the home page.
FEATURED_MAX_ROWS = 12

# The one sentence a bad banner link produces.
BANNER_LINK_MESSAGE = _("Use a link that starts with / or https://, or leave it empty.")


def banner_image_upload_path(instance: Banner, filename: str) -> str:
    """Return banners/<random>.<ext> for the uploaded banner image (D84).

    A random name avoids clashes and odd characters from the original filename,
    exactly like a product photo (D41). The extension is kept, lower-cased, so
    the file is served with the right type.
    """
    ext = Path(filename).suffix.lower()
    return f"banners/{uuid.uuid4().hex}{ext}"


def banner_display_upload_path(instance: Banner, filename: str) -> str:
    """Return banners/display/<random>.webp for the resized copy (D84)."""
    return f"banners/display/{uuid.uuid4().hex}.webp"


def validate_banner_link(value: str) -> None:
    """Refuse anything but an empty link, a shop path or an https address (D84).

    ``//evil.com`` is a protocol-relative address, a backslash and a space can
    hide a second part, and a scheme such as ``javascript:`` or ``data:`` is
    code, not a page. The rule is a whitelist: a link must be a path that starts
    with one slash, or a full ``https://`` address with a host.
    """
    link = value or ""
    if not link:
        return
    if link.startswith("/"):
        # A single leading slash only, and no backslash or whitespace anywhere.
        if not link.startswith("//") and "\\" not in link and not any(ch.isspace() for ch in link):
            return
    # urlparse splits the address; an empty netloc means there is no host.
    elif link.startswith("https://") and urlparse(link).netloc:
        return
    raise ValidationError(BANNER_LINK_MESSAGE)


class Banner(TimeStampedModel):
    """One image banner on the home page (D84).

    A banner is never shown outside its window: an inactive banner, one that has
    not started and one that has ended are all skipped by ``home.visible_banners``.
    The original image is kept as the fallback, and the WebP ``display`` copy is
    what the page uses when it exists.
    """

    # Shown to staff only; never printed on a customer page.
    title = models.CharField(max_length=100)
    image = models.ImageField(
        max_length=255,
        upload_to=banner_image_upload_path,
        validators=[validate_image_extension, validate_image_size, validate_image_content],
        width_field="width",
        height_field="height",
    )
    # Filled in by Django when the image is saved, so the page can reserve space.
    width = models.PositiveIntegerField(null=True, blank=True, editable=False)
    height = models.PositiveIntegerField(null=True, blank=True, editable=False)
    # The WebP copy made on save; an empty value means "use the original".
    display = models.ImageField(
        upload_to=banner_display_upload_path,
        null=True,
        blank=True,
        editable=False,
    )
    alt_text = models.CharField(max_length=200)
    # Empty means "not a link". clean() below refuses anything else suspicious.
    link_url = models.CharField(max_length=200, blank=True, default="")
    sort_order = models.PositiveSmallIntegerField(default=0)
    starts_at = models.DateTimeField(null=True, blank=True)
    # A missing end never expires the banner.
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            # Both times are optional; when both are set the end must be later.
            models.CheckConstraint(
                condition=(
                    models.Q(starts_at__isnull=True)
                    | models.Q(ends_at__isnull=True)
                    | models.Q(ends_at__gt=models.F("starts_at"))
                ),
                name="banner_ends_after_starts",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Make the WebP display copy when the image is new or changed (D84).

        A failed conversion only logs a warning: the original image is still
        there, so the banner still shows (the same rule as ProductImage).
        """
        image_changed = True
        if self.pk:
            try:
                old = Banner.objects.only("image").get(pk=self.pk)
                image_changed = old.image.name != (self.image.name if self.image else "")
            except Banner.DoesNotExist:
                image_changed = True
        super().save(*args, **kwargs)
        if self.image and image_changed:
            # Imported here so the model module stays free of Pillow at import
            # time, exactly as ProductImage does it.
            from apps.catalog import images

            display = None
            try:
                display = images.create_image(
                    self.image,
                    BANNER_DISPLAY_MAX_SIDE,
                    BANNER_DISPLAY_QUALITY,
                )
            except Exception:
                logger.warning("Could not create a banner display image.", exc_info=True)
            if display is not None:
                self.display.save(
                    Path(banner_display_upload_path(self, "")).name,
                    display,
                    save=False,
                )
                super().save(update_fields=["display"])

    def clean(self) -> None:
        """Refuse a bad link, keyed to the field so the admin shows it there.

        ``full_clean()`` is what the admin form runs, so this one check is used
        by the admin and by any plain ``full_clean()`` in code.
        """
        super().clean()
        try:
            validate_banner_link(self.link_url)
        except ValidationError as error:
            raise ValidationError({"link_url": error.messages}) from None

    @property
    def is_external(self) -> bool:
        """True when the link leaves this shop, so the page adds rel=noopener."""
        return self.link_url.startswith("https://")


class FeaturedProduct(TimeStampedModel):
    """One product staff hand-place on the home page (D84).

    The row only says which product and in what order; whether the product may
    be shown at all is still the storefront visibility rule.
    """

    product = models.OneToOneField(
        "catalog.Product",
        on_delete=models.CASCADE,
        related_name="featured_row",
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "featured product"

    def __str__(self) -> str:
        return str(self.product)

    def clean(self) -> None:
        """Refuse a 13th row; editing a stored row never counts itself (D84)."""
        super().clean()
        if self.pk is not None:
            return
        if FeaturedProduct.objects.count() >= FEATURED_MAX_ROWS:
            raise ValidationError(
                _("You can feature at most %(max)d products. Remove one before adding another.")
                % {"max": FEATURED_MAX_ROWS}
            )
