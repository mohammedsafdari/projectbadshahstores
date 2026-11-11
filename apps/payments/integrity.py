"""Read-only integrity check for online money and refunds (D78).

Nothing here writes or locks anything. The database already refuses a refund
of zero, a negative fee, a fee on a shop cancellation, a second open refund for
one payment and a processed time on a refund that is not processed. This module
proves the rules only the services enforce: every refund adds up to its
payment, a cancelled order with captured money has a refund, and the
``needs_refund`` flag agrees with the refund rows.
"""

from dataclasses import dataclass

from apps.orders.models import Order

from .models import Payment, Refund

# The payment statuses that mean money was taken, so a refund may point at it.
_CAPTURED_STATUSES = (
    Payment.Status.CAPTURED,
    Payment.Status.REFUND_PENDING,
    Payment.Status.REFUNDED,
    Payment.Status.PARTIALLY_REFUNDED,
)


# A dataclass writes __init__, __repr__ and == from the field list, and
# frozen=True makes an instance read-only.
@dataclass(frozen=True)
class PaymentProblem:
    """One finding of check_payment_integrity."""

    payment_id: int
    code: str
    detail: str


def check_payment_integrity() -> list[PaymentProblem]:
    """Return every payment problem found. An empty list means money is clean."""
    problems: list[PaymentProblem] = []
    refunds = list(Refund.objects.select_related("payment", "payment__order").order_by("pk"))

    # Three lookups built in one pass over the refunds, so the payment loop
    # below needs no query per row.
    first_refund: dict[int, Refund] = {}
    open_refunds: set[int] = set()
    processed_refunds: set[int] = set()
    for refund in refunds:
        first_refund.setdefault(refund.payment_id, refund)
        if refund.status in (Refund.Status.REQUESTED, Refund.Status.SENT):
            open_refunds.add(refund.payment_id)
        if refund.status == Refund.Status.PROCESSED:
            processed_refunds.add(refund.payment_id)

        if refund.amount + refund.fee_amount != refund.payment.amount:
            problems.append(
                PaymentProblem(
                    payment_id=refund.payment_id,
                    code="refund_total_mismatch",
                    detail=(
                        f"Refund {refund.pk} pays {refund.amount} and keeps "
                        f"{refund.fee_amount}, but payment {refund.payment_id} is "
                        f"{refund.payment.amount}."
                    ),
                )
            )
        if refund.kind == Refund.Kind.SHOP_CANCEL and refund.fee_amount != 0:
            problems.append(
                PaymentProblem(
                    payment_id=refund.payment_id,
                    code="fee_on_shop_cancel",
                    detail=f"Refund {refund.pk} is a shop cancellation with a fee.",
                )
            )
        if refund.payment.status not in _CAPTURED_STATUSES:
            problems.append(
                PaymentProblem(
                    payment_id=refund.payment_id,
                    code="refund_on_uncaptured_payment",
                    detail=(
                        f"Refund {refund.pk} points at payment {refund.payment_id}, "
                        f"which is {refund.payment.status}."
                    ),
                )
            )

    for payment in Payment.objects.filter(status=Payment.Status.CAPTURED).select_related("order"):
        stored_refund = first_refund.get(payment.pk)
        if payment.order.status == Order.Status.CANCELLED and stored_refund is None:
            problems.append(
                PaymentProblem(
                    payment_id=payment.pk,
                    code="cancelled_order_money_not_refunded",
                    detail=(
                        f"Payment {payment.pk} is captured on cancelled order "
                        f"{payment.order_id} and has no refund."
                    ),
                )
            )
        if payment.needs_refund and payment.pk in processed_refunds:
            problems.append(
                PaymentProblem(
                    payment_id=payment.pk,
                    code="refunded_payment_still_flagged",
                    detail=f"Payment {payment.pk} is refunded but still needs a refund.",
                )
            )
        if payment.pk in open_refunds and not payment.needs_refund:
            problems.append(
                PaymentProblem(
                    payment_id=payment.pk,
                    code="open_refund_flag_cleared",
                    detail=(f"Payment {payment.pk} has an open refund but does not need one."),
                )
            )

    return problems
