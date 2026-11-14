"""Orders app models: Order, OrderItem and the append-only status log (D64).

Money is ``Decimal`` only. Every foreign key is ``PROTECT`` so history cannot
be deleted by accident. Business rules (transitions, placement, cancellation)
live in ``apps/orders/services.py``, never in ``save()``.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.catalog.models import ProductVariant
from apps.core.models import AppendOnlyError, TimeStampedModel


class Order(TimeStampedModel):
    """One customer order with frozen address and money snapshots (D64)."""

    class Status(models.TextChoices):
        PLACED = "PLACED", "Placed"
        PAYMENT_PENDING = "PAYMENT_PENDING", "Payment pending"
        PAID = "PAID", "Paid"
        CONFIRMED = "CONFIRMED", "Confirmed"
        PACKING = "PACKING", "Packing"
        OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY", "Out for delivery"
        DELIVERED = "DELIVERED", "Delivered"
        CANCELLED = "CANCELLED", "Cancelled"
        PAYMENT_FAILED = "PAYMENT_FAILED", "Payment failed"
        REFUND_PENDING = "REFUND_PENDING", "Refund pending"
        REFUNDED = "REFUNDED", "Refunded"
        PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED", "Partially refunded"

    class PaymentMethod(models.TextChoices):
        COD = "COD", "Cash on delivery"
        CREDIT = "CREDIT", "Credit (khata)"
        ONLINE = "ONLINE", "Pay online"

    class SubstitutionPreference(models.TextChoices):
        SUBSTITUTE = "SUBSTITUTE", "Substitute"
        REFUND = "REFUND", "Refund"
        CALL_ME = "CALL_ME", "Call me"

    order_number = models.CharField(max_length=32, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    # The reservation group this order adopted (D63). Unique, so re-submitting
    # one checkout can never create a second order (D64).
    checkout_group = models.UUIDField(unique=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PLACED,
    )
    payment_method = models.CharField(
        max_length=10,
        choices=PaymentMethod.choices,
        default=PaymentMethod.COD,
    )

    # A frozen copy of the chosen Address (D57). Later edits or archiving of
    # the address never change a placed order.
    ship_name = models.CharField(max_length=200)
    ship_phone = models.CharField(max_length=16)
    ship_line1 = models.CharField(max_length=200)
    ship_line2 = models.CharField(max_length=200, blank=True, default="")
    ship_landmark = models.CharField(max_length=200, blank=True, default="")
    ship_city = models.CharField(max_length=100)
    ship_state = models.CharField(max_length=100)
    ship_pincode = models.CharField(max_length=6)
    ship_label = models.CharField(max_length=10, blank=True, default="")

    # Money is Decimal INR with two places, never float (D3).
    item_total = models.DecimalField(max_digits=12, decimal_places=2)
    delivery_fee = models.DecimalField(max_digits=12, decimal_places=2)
    # A coupon's discounts are frozen here (D82). ``delivery_fee`` stays the
    # full fee the cart worked out, so a free-delivery coupon has its own
    # field and the one equation is
    # grand_total = item_total - discount_total + delivery_fee - delivery_discount.
    discount_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    delivery_discount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    # The code that was used, in capitals; blank for an order with no coupon.
    coupon_code = models.CharField(max_length=20, blank=True, default="")
    grand_total = models.DecimalField(max_digits=12, decimal_places=2)
    savings_total = models.DecimalField(max_digits=12, decimal_places=2)
    # How much of this order is paid from the customer's khata (D74). Zero for
    # a cash-on-delivery order; between one paisa and the total for a split one.
    credit_applied = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    # How much of this order is paid online (D77). Zero for a cash or khata
    # order; between one paisa and the total for an online or split one.
    online_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    amount_due_on_delivery = models.DecimalField(max_digits=12, decimal_places=2)
    # The percent kept from a refund when the customer cancels a paid online
    # order. It is copied from ShopSettings at placement and never changes
    # afterwards, so an old order always refunds on its own terms (D78).
    refund_fee_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    promised_delivery_date = models.DateField()
    delivery_is_same_day = models.BooleanField(default=False)
    substitution_preference = models.CharField(
        max_length=20,
        choices=SubstitutionPreference.choices,
        default=SubstitutionPreference.CALL_ME,
    )
    customer_note = models.CharField(max_length=300, blank=True, default="")
    cancel_reason = models.CharField(max_length=200, blank=True, default="")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    # The nested Status names are not visible inside Meta, so the conditions
    # below use the plain stored strings.
    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(
                fields=["user", "created_at"],
                name="orders_order_user_created_idx",
            ),
            models.Index(fields=["status"], name="orders_order_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(item_total__gte=0)
                    & models.Q(delivery_fee__gte=0)
                    & models.Q(grand_total__gte=0)
                    & models.Q(savings_total__gte=0)
                    & models.Q(amount_due_on_delivery__gte=0)
                ),
                name="order_money_gte_0",
            ),
            # The one equation every order amount must satisfy (D82).
            models.CheckConstraint(
                condition=models.Q(
                    grand_total=(
                        models.F("item_total")
                        - models.F("discount_total")
                        + models.F("delivery_fee")
                        - models.F("delivery_discount")
                    )
                ),
                name="order_grand_total_matches",
            ),
            # A discount is money: never negative, never more than it discounts.
            models.CheckConstraint(
                condition=models.Q(discount_total__gte=0) & models.Q(delivery_discount__gte=0),
                name="order_discounts_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_total__lte=models.F("item_total")),
                name="order_item_discount_lte_item_total",
            ),
            models.CheckConstraint(
                condition=models.Q(delivery_discount__lte=models.F("delivery_fee")),
                name="order_delivery_discount_lte_fee",
            ),
            # The khata part is money: never negative and never above the total.
            models.CheckConstraint(
                condition=models.Q(credit_applied__gte=0),
                name="order_credit_applied_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(credit_applied__lte=models.F("grand_total")),
                name="order_credit_applied_lte_grand_total",
            ),
            # The online part is money too: never negative and never above the
            # total. The two paid parts together never exceed the total (D77).
            models.CheckConstraint(
                condition=models.Q(online_amount__gte=0),
                name="order_online_amount_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(online_amount__lte=models.F("grand_total")),
                name="order_online_amount_lte_grand_total",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    credit_applied__lte=models.F("grand_total") - models.F("online_amount")
                ),
                name="order_payment_split_lte_grand_total",
            ),
            # Whatever the khata and the online payment do not cover is
            # collected at the door (D74, D77).
            models.CheckConstraint(
                condition=models.Q(
                    amount_due_on_delivery=(
                        models.F("grand_total")
                        - models.F("credit_applied")
                        - models.F("online_amount")
                    )
                ),
                name="order_amount_due_matches_split",
            ),
            # The method must agree with the parts:
            # - cash: no khata and no online part;
            # - khata: a khata part, with or without an online part;
            # - online: an online part and no khata part.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        payment_method="COD",
                        credit_applied=0,
                        online_amount=0,
                    )
                    | models.Q(payment_method="CREDIT", credit_applied__gt=0)
                    | models.Q(
                        payment_method="ONLINE",
                        credit_applied=0,
                        online_amount__gt=0,
                    )
                ),
                name="order_payment_split_matches_method",
            ),
            # The refund fee is a percentage, never below zero or above ten.
            models.CheckConstraint(
                condition=models.Q(refund_fee_percent__gte=0),
                name="order_refund_fee_percent_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(refund_fee_percent__lte=10),
                name="order_refund_fee_percent_lte_10",
            ),
            # cancelled_at is set exactly when the status is CANCELLED.
            models.CheckConstraint(
                condition=(
                    models.Q(status="CANCELLED", cancelled_at__isnull=False)
                    | (~models.Q(status="CANCELLED") & models.Q(cancelled_at__isnull=True))
                ),
                name="order_cancelled_at_matches_status",
            ),
            # delivered_at is set exactly when the status is DELIVERED.
            models.CheckConstraint(
                condition=(
                    models.Q(status="DELIVERED", delivered_at__isnull=False)
                    | (~models.Q(status="DELIVERED") & models.Q(delivered_at__isnull=True))
                ),
                name="order_delivered_at_matches_status",
            ),
        ]

    def __str__(self) -> str:
        return self.order_number


class OrderItem(TimeStampedModel):
    """One frozen order line: variant, quantity and purchase-time snapshots."""

    class ItemStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        FULFILLED = "FULFILLED", "Fulfilled"
        CANCELLED = "CANCELLED", "Cancelled"

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="items",
    )
    variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.PROTECT,
        related_name="order_items",
    )

    # Snapshots copied at purchase time. The live variant is never read again
    # for display or totals (D64).
    sku_code = models.CharField(max_length=40)
    product_name = models.CharField(max_length=200)
    pack_label = models.CharField(max_length=60)
    unit = models.CharField(max_length=10, choices=ProductVariant.Unit.choices)
    hsn_code = models.CharField(max_length=8)
    gst_rate_percent = models.DecimalField(max_digits=5, decimal_places=2)
    mrp = models.DecimalField(max_digits=12, decimal_places=2)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)

    quantity_ordered = models.DecimalField(max_digits=12, decimal_places=3)
    # Packing may weigh more than ordered, so fulfilled may exceed ordered.
    quantity_fulfilled = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0,
    )
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    item_status = models.CharField(
        max_length=20,
        choices=ItemStatus.choices,
        default=ItemStatus.PENDING,
    )

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity_ordered__gt=0),
                name="orderitem_quantity_ordered_gt_0",
            ),
            # Fulfilled may exceed ordered, but never goes below zero.
            models.CheckConstraint(
                condition=models.Q(quantity_fulfilled__gte=0),
                name="orderitem_quantity_fulfilled_gte_0",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(mrp__gte=0) & models.Q(unit_price__gte=0) & models.Q(line_total__gte=0)
                ),
                name="orderitem_prices_gte_0",
            ),
            models.UniqueConstraint(
                fields=["order", "variant"],
                name="orderitem_unique_variant_per_order",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.quantity_ordered} x {self.sku_code}"


# QuerySet-level update/delete/bulk_update skip save(), so they need the same
# guard as AuditLog and the inventory ledger.
class OrderStatusLogQuerySet(models.QuerySet["OrderStatusLog"]):
    def update(self, **kwargs: Any) -> int:
        raise AppendOnlyError("Order status log entries cannot be updated.")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError("Order status log entries cannot be deleted.")

    def bulk_update(
        self,
        objs: Iterable[OrderStatusLog],
        fields: Iterable[str],
        batch_size: int | None = None,
    ) -> int:
        raise AppendOnlyError("Order status log entries cannot be bulk-updated.")


class OrderStatusLog(TimeStampedModel):
    """One append-only order status change: who, when, from and to (D10, D64)."""

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="status_logs",
    )
    # Blank only on the first row, which records the move to PLACED.
    from_status = models.CharField(max_length=20, blank=True, default="")
    to_status = models.CharField(max_length=20, choices=Order.Status.choices)
    # Null for system actions; PROTECT keeps a user with history from deletion.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="order_status_logs",
    )
    note = models.CharField(max_length=200, blank=True, default="")

    objects = OrderStatusLogQuerySet.as_manager()

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self) -> str:
        return f"{self.from_status or '-'} -> {self.to_status}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        # ``_state.adding`` is True only for the first write of a new row.
        if not self._state.adding:
            raise AppendOnlyError("Order status log entries cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError("Order status log entries cannot be deleted.")


class CodCollection(TimeStampedModel):
    """The cash one staff member collected at the door for one order (D79).

    There is at most one row per order: the one-to-one column is the
    "one collection per order" rule in the database itself. The amount is
    always ``Decimal`` and always above zero, and the foreign keys are
    ``PROTECT`` so a collected amount and the staff member who took it can
    never be deleted by accident.
    """

    order = models.OneToOneField(
        Order,
        on_delete=models.PROTECT,
        related_name="cod_collection",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    collected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cod_collections",
    )
    # The moment the cash was taken. The service sets it; the default only
    # covers a row made outside the service.
    collected_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-collected_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="codcollection_amount_gt_0",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.order_id} {self.amount}"


class Invoice(TimeStampedModel):
    """The one GST tax invoice of one order, frozen when it goes out (D13, D80).

    Every value the printed page shows is copied here, so a later edit to
    ``ShopSettings`` or to a product can never rewrite an issued invoice. Money
    is ``Decimal`` and the number is unique: a cancelled invoice keeps its
    number, because the series must stay gapless (D14).
    """

    order = models.OneToOneField(
        Order,
        on_delete=models.PROTECT,
        related_name="invoice",
    )
    # The GST rule caps an invoice number at 16 characters (D14).
    invoice_number = models.CharField(max_length=16, unique=True)
    issued_at = models.DateTimeField()

    # The seller, copied from ShopSettings when the invoice is issued.
    seller_name = models.CharField(max_length=200)
    seller_address = models.TextField()
    seller_gstin = models.CharField(max_length=15)
    seller_state = models.CharField(max_length=100)
    seller_state_code = models.CharField(max_length=2)

    # The buyer, copied from the frozen order (D57).
    buyer_name = models.CharField(max_length=200)
    buyer_phone = models.CharField(max_length=16)
    buyer_address = models.TextField()
    place_of_supply = models.CharField(max_length=100)

    # Totals over every line, the delivery fee line included.
    taxable_total = models.DecimalField(max_digits=12, decimal_places=2)
    cgst_total = models.DecimalField(max_digits=12, decimal_places=2)
    sgst_total = models.DecimalField(max_digits=12, decimal_places=2)
    gst_total = models.DecimalField(max_digits=12, decimal_places=2)
    # The fee actually charged, so it is already net of a delivery discount.
    delivery_fee = models.DecimalField(max_digits=12, decimal_places=2)
    # The coupon's discount, frozen with the invoice (D82).
    discount_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    coupon_code = models.CharField(max_length=20, blank=True, default="")
    grand_total = models.DecimalField(max_digits=12, decimal_places=2)

    # A cancelled invoice is marked, never deleted or renumbered (D80).
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        ordering = ["-issued_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(taxable_total__gte=0)
                    & models.Q(cgst_total__gte=0)
                    & models.Q(sgst_total__gte=0)
                    & models.Q(gst_total__gte=0)
                    & models.Q(delivery_fee__gte=0)
                    & models.Q(discount_total__gte=0)
                    & models.Q(grand_total__gte=0)
                ),
                name="invoice_money_gte_0",
            ),
            # The GST total is exactly the two halves added together.
            models.CheckConstraint(
                condition=models.Q(cgst_total=models.F("gst_total") - models.F("sgst_total")),
                name="invoice_cgst_plus_sgst_matches",
            ),
            # Each line total is its taxable value plus its CGST and SGST, so
            # the sum of the line totals is the taxable total plus the GST
            # total. ``find_invoice_problems`` proves the line sum itself.
            models.CheckConstraint(
                condition=models.Q(grand_total=models.F("taxable_total") + models.F("gst_total")),
                name="invoice_grand_total_matches",
            ),
        ]

    def __str__(self) -> str:
        return self.invoice_number


class InvoiceLine(TimeStampedModel):
    """One printed line of an invoice: a product line or the delivery fee (D80).

    ``rate`` and ``line_total`` include GST, the way the shop prices its goods;
    ``taxable_value``, ``cgst`` and ``sgst`` are the split inside them.
    """

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name="lines",
    )
    # The order line this printed line was built from, so a report can reach the
    # variant behind it (D86). Empty on the delivery-fee line, and on every
    # invoice written before this link existed; such a line is reported as
    # "unknown" rather than guessed from its name.
    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="invoice_lines",
    )
    description = models.CharField(max_length=200)
    # A fee line has no HSN code and no unit, so both may be blank.
    hsn_code = models.CharField(max_length=8, blank=True, default="")
    unit = models.CharField(max_length=10, blank=True, default="")
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    rate = models.DecimalField(max_digits=12, decimal_places=2)
    # How much of the item discount this line carries (D82). ``line_total`` is
    # already net of it, and ``rate`` stays the full unit price.
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    gst_rate_percent = models.DecimalField(max_digits=5, decimal_places=2)
    taxable_value = models.DecimalField(max_digits=12, decimal_places=2)
    cgst = models.DecimalField(max_digits=12, decimal_places=2)
    sgst = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    # True for the one delivery-fee line, so a report can leave it out.
    is_fee_line = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(quantity__gte=0)
                    & models.Q(rate__gte=0)
                    & models.Q(discount_amount__gte=0)
                    & models.Q(gst_rate_percent__gte=0)
                    & models.Q(taxable_value__gte=0)
                    & models.Q(cgst__gte=0)
                    & models.Q(sgst__gte=0)
                    & models.Q(line_total__gte=0)
                ),
                name="invoiceline_money_gte_0",
            ),
            # The printed total is exactly its parts added together.
            models.CheckConstraint(
                condition=models.Q(
                    line_total=models.F("taxable_value") + models.F("cgst") + models.F("sgst")
                ),
                name="invoiceline_total_matches",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.quantity} x {self.description}"
