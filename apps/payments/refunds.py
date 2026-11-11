"""Refunds for cancelled online orders, and the reconciliation of money (D78).

A refund is created only from a captured payment, for the whole payment
amount, and it is sent only from a Celery task after the cancellation commits.
The provider call runs outside the database lock: the refund is claimed first
(attempt count and time), then the provider is called with a stable
idempotency key, then the result is stored under the lock again.

The fixed lock order is the order row, then the payment row, then the refund
row, so a cancel, a webhook and the retry job cannot deadlock.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.notifications.messages import (
    EVENT_REFUND_PROCESSED,
    EVENT_STAFF_REFUND_FAILED,
)
from apps.notifications.order_notices import notify_order_event
from apps.orders.models import Order
from apps.payments.gateway import (
    REFUND_FAILED,
    REFUND_PROCESSED,
    GatewayRefund,
    PaymentGatewayError,
    RefundRejectedError,
    get_gateway,
)
from apps.payments.models import Payment, Refund

logger = logging.getLogger(__name__)

# A SENT refund is still with the provider for this long. After that, a crash
# between the claim and the provider call may have left it unsent, so a person
# may record it by hand (D79 amendment, Slice 42c).
REFUND_STUCK_AFTER = timedelta(minutes=30)


class RefundError(ValueError):
    """Base error for every refund refusal."""


class RefundNotAllowedError(RefundError):
    """This payment cannot be refunded, or this kind of refund is unknown."""


class RefundNotFoundError(RefundError):
    """No refund matches the id."""


class RefundNoteRequiredError(RefundError):
    """A refund marked by hand must say why."""


class RefundAlreadySettledError(RefundError):
    """This refund is already processed, or the provider is sending it."""


@dataclass(frozen=True)
class _Claim:
    """What the provider call needs, copied while the rows were locked."""

    refund_id: int
    gateway_payment_id: str
    amount: Decimal
    idempotency_key: str


def _now(now: datetime | None) -> datetime:
    return now if now is not None else timezone.now()


def refund_is_stuck(refund: Refund | None, *, now: datetime | None = None) -> bool:
    """Whether a SENT refund is old enough to record by hand (Slice 42c).

    A missing attempt time also counts as stuck: the claim may have crashed
    before writing it. Exactly ``REFUND_STUCK_AFTER`` old is not yet stuck, so
    the boundary is refused.
    """
    if refund is None or refund.status != Refund.Status.SENT:
        return False
    attempted = refund.last_attempt_at
    if attempted is None:
        return True
    return attempted < _now(now) - REFUND_STUCK_AFTER


def _fee_for(payment: Payment, kind: str) -> Decimal:
    """The part of this payment the shop keeps on a customer cancellation.

    Only a customer cancellation may keep a fee; a shop cancellation always
    returns the whole payment. Otherwise the fee is a percentage of the
    payment, rounded half-up to the paisa, and it can never exceed the payment
    itself. The percent sits on the order, so changing ShopSettings later never
    rewrites an old order (D78).
    """
    if kind != Refund.Kind.CUSTOMER_CANCEL:
        return Decimal("0.00")
    percent = payment.order.refund_fee_percent
    if percent <= Decimal("0.00"):
        return Decimal("0.00")
    fee = (payment.amount * percent / Decimal("100")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return min(fee, payment.amount)


def _notify_later(order_id: int, event: str) -> None:
    """Send one refund message after the current transaction commits."""

    # A nested function binds the id and event now; a lambda would read the
    # caller's variables when it finally runs.
    def send_notice() -> None:
        notify_order_event(order_id, event)

    transaction.on_commit(send_notice)


def send_refund_later(refund_id: int) -> None:
    """Queue this refund for the Celery task once the transaction commits.

    The refund is created in the same transaction as the cancellation, so
    calling the provider here would refund money for an order that might still
    roll back. A broker outage is logged, never raised: the periodic
    reconciliation run finds the REQUESTED refund and sends it later (D78).
    """

    def enqueue() -> None:
        # Imported inside the callback so the tasks module is not needed until
        # a refund actually exists.
        from apps.payments.tasks import send_refunded_payment

        try:
            send_refunded_payment.delay(refund_id)
        except Exception:
            logger.exception("Refund task could not be queued: refund=%s", refund_id)

    transaction.on_commit(enqueue)


def _apply_refund_status(
    refund: Refund,
    payment: Payment,
    *,
    kind: str,
    provider_refund_id: str,
    safe: dict[str, object],
    failure_reason: str,
    now: datetime,
) -> None:
    """Write one provider answer onto a locked refund (D78).

    A repeated answer for the same status changes nothing, so a webhook retry
    cannot send a second customer message.
    """
    if refund.status == Refund.Status.PROCESSED:
        # Money that went back never moves again, whatever a late event says.
        return
    update_fields = ["provider_refund_id", "raw_response", "updated_at"]
    if provider_refund_id:
        refund.provider_refund_id = provider_refund_id
    if safe:
        refund.raw_response = safe

    if kind == REFUND_PROCESSED:
        if refund.status == Refund.Status.PROCESSED:
            return
        refund.status = Refund.Status.PROCESSED
        refund.processed_at = now
        if refund.sent_at is None:
            refund.sent_at = now
        refund.failure_reason = ""
        update_fields += ["status", "processed_at", "sent_at", "failure_reason"]
        # The refund duty is over only when the provider says the money went
        # back. A REQUESTED or SENT refund keeps the flag (D78).
        payment.needs_refund = False
        payment.save(update_fields=["needs_refund", "updated_at"])
        _notify_later(payment.order_id, EVENT_REFUND_PROCESSED)
    elif kind == REFUND_FAILED:
        if refund.status == Refund.Status.FAILED:
            return
        refund.status = Refund.Status.FAILED
        refund.failure_reason = failure_reason[:200] or "The refund failed."
        update_fields += ["status", "failure_reason"]
        _notify_later(payment.order_id, EVENT_STAFF_REFUND_FAILED)
    else:
        # Still on its way at the provider.
        if refund.status == Refund.Status.SENT:
            return
        refund.status = Refund.Status.SENT
        if refund.sent_at is None:
            refund.sent_at = now
        update_fields.append("status")
        if "sent_at" not in update_fields:
            update_fields.append("sent_at")

    refund.save(update_fields=update_fields)


def apply_gateway_refund(
    refund: Refund,
    payment: Payment,
    *,
    kind: str,
    amount: Decimal,
    provider_refund_id: str = "",
    safe: dict[str, object] | None = None,
    failure_reason: str = "",
    now: datetime,
) -> str | None:
    """Apply one verified refund event; return an error message or None.

    The caller has already locked the order, the payment and the refund inside
    one transaction. An amount that does not match the stored refund changes
    nothing and is reported to the caller, which logs it (D78).
    """
    if amount != refund.amount:
        return "Amount does not match the refund"
    _apply_refund_status(
        refund,
        payment,
        kind=kind,
        provider_refund_id=provider_refund_id,
        safe=safe or {},
        failure_reason=failure_reason,
        now=now,
    )
    return None


def ensure_refund(
    payment: Payment,
    *,
    kind: str,
    now: datetime | None = None,
) -> Refund:
    """Create the one refund for a captured payment, or return the existing one.

    Idempotent: a payment has at most one refund, whatever its status, so a
    repeated cancel or a second reconciliation run writes nothing. The
    idempotency key is built from the payment id alone and never changes.
    """
    if kind not in Refund.Kind.values:
        raise RefundNotAllowedError(f"Unknown refund kind {kind!r}.")
    with transaction.atomic():
        # Lock order first, then payment, then refund (D78).
        Order.objects.select_for_update().filter(pk=payment.order_id).first()
        stored_payment = (
            Payment.objects.select_for_update()
            .select_related("order")
            .filter(pk=payment.pk)
            .first()
        )
        if stored_payment is None:
            raise RefundNotFoundError("Payment not found.")
        existing = (
            Refund.objects.select_for_update().filter(payment=stored_payment).order_by("pk").first()
        )
        if existing is not None:
            return existing
        if stored_payment.status != Payment.Status.CAPTURED:
            raise RefundNotAllowedError("Only a captured payment can be refunded.")
        fee = _fee_for(stored_payment, kind)
        amount = stored_payment.amount - fee
        if amount <= Decimal("0.00"):
            raise RefundNotAllowedError("There is nothing left to refund.")
        refund = Refund.objects.create(
            payment=stored_payment,
            kind=kind,
            status=Refund.Status.REQUESTED,
            amount=amount,
            fee_amount=fee,
            idempotency_key=f"refund-{stored_payment.pk}",
            raw_response={},
        )
        if not stored_payment.needs_refund:
            # A refund row means the duty exists. The flag is cleared again
            # only when the provider says the money went back (D78).
            stored_payment.needs_refund = True
            stored_payment.save(update_fields=["needs_refund", "updated_at"])
        return refund


def get_refund(refund_id: int) -> Refund:
    """One refund by id, or a clear error."""
    refund = Refund.objects.filter(pk=refund_id).first()
    if refund is None:
        raise RefundNotFoundError("Refund not found.")
    return refund


def latest_refund(order: Order) -> Refund | None:
    """The most recent refund of any payment on this order, if there is one."""
    return (
        Refund.objects.filter(payment__order=order)
        .select_related("payment")
        .order_by("-created_at", "-pk")
        .first()
    )


def _lock_claim_rows(refund_id: int) -> tuple[Payment, Refund] | None:
    """Lock the order, then the payment, then the refund (D78)."""
    lookup = Refund.objects.filter(pk=refund_id).values("payment_id").first()
    if lookup is None:
        raise RefundNotFoundError("Refund not found.")
    payment_lookup = Payment.objects.filter(pk=lookup["payment_id"]).values("order_id").first()
    if payment_lookup is None:
        raise RefundNotFoundError("Refund not found.")
    Order.objects.select_for_update().filter(pk=payment_lookup["order_id"]).first()
    payment = Payment.objects.select_for_update().filter(pk=lookup["payment_id"]).first()
    refund = Refund.objects.select_for_update().filter(pk=refund_id).first()
    if payment is None or refund is None:
        return None
    return payment, refund


def _mark_failed_locked(refund: Refund, reason: str, now: datetime) -> None:
    """Mark a locked refund FAILED and ask a person to look at it."""
    refund.status = Refund.Status.FAILED
    refund.failure_reason = reason[:200] or "The refund failed."
    refund.raw_response = refund.raw_response or {}
    refund.save(update_fields=["status", "failure_reason", "raw_response", "updated_at"])
    _notify_later(refund.payment.order_id, EVENT_STAFF_REFUND_FAILED)
    logger.warning("Refund failed: refund=%s reason=%s", refund.pk, reason[:200])


def _claim_refund(refund_id: int, *, now: datetime) -> _Claim | None:
    """Count one attempt and return the facts the provider call needs.

    ``None`` means there is nothing to send: the refund is already SENT,
    PROCESSED or FAILED, the payment is not captured, or the attempt limit is
    reached. The provider call must happen after this transaction commits.
    """
    with transaction.atomic():
        locked = _lock_claim_rows(refund_id)
        if locked is None:
            raise RefundNotFoundError("Refund not found.")
        payment, refund = locked
        if refund.status != Refund.Status.REQUESTED:
            return None
        max_attempts = int(getattr(settings, "REFUND_MAX_ATTEMPTS", 3))
        if payment.status != Payment.Status.CAPTURED:
            _mark_failed_locked(refund, "The payment is not captured.", now)
            return None
        if refund.attempt_count >= max_attempts:
            _mark_failed_locked(refund, "Too many refund attempts.", now)
            return None
        refund.attempt_count += 1
        refund.last_attempt_at = now
        refund.failure_reason = ""
        # The claim marker: a second worker sees SENT and stops before calling
        # the provider, so one refund is never sent twice (D79 amendment). A
        # provider error puts it back to REQUESTED below.
        refund.status = Refund.Status.SENT
        refund.save(
            update_fields=[
                "status",
                "attempt_count",
                "last_attempt_at",
                "failure_reason",
                "updated_at",
            ]
        )
        return _Claim(
            refund_id=refund.pk,
            gateway_payment_id=payment.gateway_payment_id,
            amount=refund.amount,
            idempotency_key=refund.idempotency_key,
        )


def _record_attempt_failure(
    claim: _Claim,
    reason: str,
    *,
    retryable: bool,
    now: datetime,
) -> Refund:
    """Store a failed provider call; a transient error keeps REQUESTED."""
    with transaction.atomic():
        locked = _lock_claim_rows(claim.refund_id)
        if locked is None:
            raise RefundNotFoundError("Refund not found.")
        _payment, refund = locked
        max_attempts = int(getattr(settings, "REFUND_MAX_ATTEMPTS", 3))
        if not retryable or refund.attempt_count >= max_attempts:
            _mark_failed_locked(refund, reason, now)
            return refund
        # The claim moved it to SENT; a retryable failure hands it back to the
        # next reconciliation run.
        refund.status = Refund.Status.REQUESTED
        refund.failure_reason = reason[:200]
        refund.save(update_fields=["status", "failure_reason", "updated_at"])
        logger.warning("Refund attempt failed: refund=%s", refund.pk)
        return refund


def _record_result(claim: _Claim, result: GatewayRefund, *, now: datetime) -> Refund:
    """Store the provider's answer for one claimed refund."""
    with transaction.atomic():
        locked = _lock_claim_rows(claim.refund_id)
        if locked is None:
            raise RefundNotFoundError("Refund not found.")
        payment, refund = locked
        if result.amount != refund.amount:
            # A wrong amount is a provider fault, never a reason to mark the
            # refund done. The claim left it SENT, so hand it back to the next
            # reconciliation run; a webhook that already finished it stays done.
            if refund.status == Refund.Status.SENT:
                refund.status = Refund.Status.REQUESTED
            refund.failure_reason = "The provider refunded a different amount."
            refund.save(update_fields=["status", "failure_reason", "updated_at"])
            logger.warning("Refund amount mismatch: refund=%s", refund.pk)
            return refund
        _apply_refund_status(
            refund,
            payment,
            kind=result.kind,
            provider_refund_id=result.gateway_refund_id,
            safe=result.safe,
            failure_reason=str(result.safe.get("error_description", ""))[:200],
            now=now,
        )
        return refund


def send_refund(
    refund_id: int,
    *,
    gateway=None,
    now: datetime | None = None,
) -> Refund:
    """Send one REQUESTED refund, counting the attempt first (D78).

    The provider call runs outside any transaction: a slow network reply must
    not hold the order, the payment or the refund row.
    """
    resolved_now = _now(now)
    claim = _claim_refund(refund_id, now=resolved_now)
    if claim is None:
        return get_refund(refund_id)

    try:
        provider = gateway if gateway is not None else get_gateway()
    except PaymentGatewayError:
        provider = None
    if provider is None:
        return _record_attempt_failure(
            claim,
            "No payment gateway is configured.",
            retryable=True,
            now=resolved_now,
        )

    try:
        result = provider.create_refund(
            claim.gateway_payment_id,
            claim.amount,
            claim.idempotency_key,
        )
    except RefundRejectedError as error:
        # A 4xx refusal cannot succeed on a retry.
        return _record_attempt_failure(
            claim,
            str(error),
            retryable=False,
            now=resolved_now,
        )
    except PaymentGatewayError as error:
        return _record_attempt_failure(
            claim,
            str(error),
            retryable=True,
            now=resolved_now,
        )
    return _record_result(claim, result, now=resolved_now)


def refresh_refund_from_gateway(
    refund_id: int,
    *,
    gateway=None,
    now: datetime | None = None,
) -> bool:
    """Ask the provider about one SENT refund and store its answer (D78).

    The provider call runs outside any transaction. Nothing is written when
    the provider has no answer, so the next run can ask again. A refund that
    is already PROCESSED or FAILED is left alone.
    """
    resolved_now = _now(now)
    lookup = (
        Refund.objects.filter(pk=refund_id)
        .values(
            "payment_id",
            "provider_refund_id",
            "status",
        )
        .first()
    )
    if lookup is None:
        return False
    payment_lookup = (
        Payment.objects.filter(pk=lookup["payment_id"]).values("gateway_payment_id").first()
    )
    if payment_lookup is None or not payment_lookup["gateway_payment_id"]:
        return False

    try:
        provider = gateway if gateway is not None else get_gateway()
    except PaymentGatewayError:
        provider = None
    if provider is None:
        logger.warning("Refund check skipped: refund=%s", refund_id)
        return False

    try:
        results = provider.fetch_payment_refunds(payment_lookup["gateway_payment_id"])
    except PaymentGatewayError:
        logger.warning("Refund check failed: refund=%s", refund_id)
        return False

    stored_id = str(lookup["provider_refund_id"] or "")
    # next() stops at the first match; a named refund id wins over the newest.
    chosen = next(
        (row for row in results if stored_id and row.gateway_refund_id == stored_id),
        None,
    )
    if chosen is None and results:
        chosen = results[-1]
    if chosen is None:
        return False

    with transaction.atomic():
        locked = _lock_claim_rows(refund_id)
        if locked is None:
            return False
        payment, refund = locked
        if refund.status in (Refund.Status.PROCESSED, Refund.Status.FAILED):
            return False
        error = apply_gateway_refund(
            refund,
            payment,
            kind=chosen.kind,
            amount=chosen.amount,
            provider_refund_id=chosen.gateway_refund_id,
            safe=chosen.safe,
            now=resolved_now,
        )
        if error:
            logger.warning("Refund check mismatch: refund=%s", refund.pk)
            return False
    return True


def reconcile_refunds(now: datetime | None = None) -> int:
    """Find money that must go back, send it, and check what is in flight.

    Runs every few minutes from Celery beat (D78). It only looks at money
    captured before ``PAYMENT_RECONCILE_MIN_AGE_MINUTES`` so an in-flight
    capture is never raced. Idempotent: a second run finds nothing to do.
    """
    resolved_now = _now(now)
    min_age = int(getattr(settings, "PAYMENT_RECONCILE_MIN_AGE_MINUTES", 15))
    stale_minutes = int(getattr(settings, "REFUND_STALE_MINUTES", 30))
    cutoff = resolved_now - timedelta(minutes=min_age)
    stale_cutoff = resolved_now - timedelta(minutes=stale_minutes)
    handled = 0

    # 1. Captured money on a cancelled (or otherwise finished) order that has
    # no refund row yet: a late capture the webhook could not turn into a sale.
    payment_ids = list(
        Payment.objects.filter(
            status=Payment.Status.CAPTURED,
            needs_refund=True,
            captured_at__lte=cutoff,
        )
        .exclude(refunds__isnull=False)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    for payment_id in payment_ids:
        # Read the order id without a lock, then lock the order before the
        # payment, the fixed order every money path uses (D78).
        lookup = Payment.objects.filter(pk=payment_id).values("order_id").first()
        if lookup is None:
            continue
        with transaction.atomic():
            Order.objects.select_for_update().filter(pk=lookup["order_id"]).first()
            payment = (
                Payment.objects.select_for_update()
                .select_related("order")
                .filter(pk=payment_id)
                .first()
            )
            if payment is None or not payment.needs_refund:
                continue
            if payment.status != Payment.Status.CAPTURED:
                continue
            ensure_refund(payment, kind=Refund.Kind.SHOP_CANCEL, now=resolved_now)
        handled += 1

    # 2. A requested refund has not reached the provider yet.
    requested_ids = list(
        Refund.objects.filter(status=Refund.Status.REQUESTED)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    for refund_id in requested_ids:
        result = send_refund(refund_id, now=resolved_now)
        if result.status != Refund.Status.REQUESTED:
            handled += 1

    # 3. A sent refund may have finished at the provider.
    sent_ids = list(
        Refund.objects.filter(
            status=Refund.Status.SENT,
            last_attempt_at__lte=stale_cutoff,
        )
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    for refund_id in sent_ids:
        if refresh_refund_from_gateway(refund_id, now=resolved_now):
            handled += 1

    logger.info("Reconciled %s refund action(s).", handled)
    return handled


def retry_refund(
    refund_id: int,
    *,
    gateway=None,
    now: datetime | None = None,
) -> Refund:
    """Reset one FAILED refund and send it again, on a staff click (D79).

    A FAILED refund is never retried automatically (D78); this explicit action
    clears the failure, resets the attempt count and hands the refund to
    ``send_refund``. Any other status is left to ``send_refund``, which changes
    nothing.
    """
    resolved_now = _now(now)
    with transaction.atomic():
        locked = _lock_claim_rows(refund_id)
        if locked is None:
            raise RefundNotFoundError("Refund not found.")
        _payment, refund = locked
        if refund.status == Refund.Status.FAILED:
            refund.status = Refund.Status.REQUESTED
            refund.attempt_count = 0
            refund.failure_reason = ""
            refund.raw_response = {}
            refund.save(
                update_fields=[
                    "status",
                    "attempt_count",
                    "failure_reason",
                    "raw_response",
                    "updated_at",
                ]
            )
    # The provider call runs outside every lock, inside send_refund.
    return send_refund(refund_id, gateway=gateway, now=resolved_now)


def mark_refunded_by_hand(
    refund_id: int,
    *,
    actor,
    note: str,
    now: datetime | None = None,
) -> Refund:
    """Record a refund the shop paid outside the gateway (D79).

    The money is marked PROCESSED with the actor, the time and a required note,
    and no provider call is made. A refund that is already processed, or that
    the provider may still be sending, is refused so a second payment cannot be
    recorded. A SENT refund is stuck only when its last attempt is missing or
    older than ``REFUND_STUCK_AFTER``; exactly at the limit is still refused.
    """
    cleaned = (note or "").strip()
    if not cleaned:
        raise RefundNoteRequiredError("A refund marked by hand needs a note.")
    resolved_now = _now(now)
    with transaction.atomic():
        locked = _lock_claim_rows(refund_id)
        if locked is None:
            raise RefundNotFoundError("Refund not found.")
        payment, refund = locked
        if refund.status == Refund.Status.PROCESSED:
            raise RefundAlreadySettledError("This refund is already processed.")
        if refund.status == Refund.Status.SENT and not refund_is_stuck(refund, now=resolved_now):
            raise RefundAlreadySettledError("The provider is already sending this refund.")
        refund.status = Refund.Status.PROCESSED
        refund.processed_at = resolved_now
        if refund.sent_at is None:
            refund.sent_at = resolved_now
        refund.failure_reason = ""
        refund.marked_by = actor
        refund.marked_at = resolved_now
        refund.marked_note = cleaned[:200]
        refund.save(
            update_fields=[
                "status",
                "processed_at",
                "sent_at",
                "failure_reason",
                "marked_by",
                "marked_at",
                "marked_note",
                "updated_at",
            ]
        )
        payment.needs_refund = False
        payment.save(update_fields=["needs_refund", "updated_at"])
        _notify_later(payment.order_id, EVENT_REFUND_PROCESSED)
    return refund
