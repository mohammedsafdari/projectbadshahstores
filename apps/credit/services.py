"""Credit (khata) services: the only code that writes credit rows (D67).

Every state change runs in one ``transaction.atomic()``, locks the account row
with ``select_for_update()`` and saves with
``save(update_fields=[..., "updated_at"])``. Queryset ``update()`` is never
used on the models of this app.

The lock order of the whole system is fixed (D67): the user row first, then the
stock variants in ascending id, then the order rows, and the credit account row
LAST. ``charge_order`` and ``reverse_order_charge`` follow it.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.audit import record_change
from apps.orders.models import Order

from .models import CreditAccount, CreditEntry

# Audit action names (D67 point 12), read by the admin and by the tests.
CREDIT_LIMIT_ACTION = "CREDIT_LIMIT_CHANGED"
CREDIT_ACTIVE_ACTION = "CREDIT_ACTIVE_CHANGED"

# The largest stored amount: DecimalField(12, 2) tops out just below this.
MAX_CREDIT_AMOUNT = Decimal("10000000000")
TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0.00")


class CreditError(ValueError):
    """Base error for every credit service refusal."""


class CreditAccountNotFoundError(CreditError):
    """The customer has no credit account."""


class CreditNotAvailableError(CreditError):
    """Credit cannot be used: there is no account or it is suspended."""


class CreditLimitExceededError(CreditError):
    """A charge is above the available credit, or a payment above the balance."""


class InvalidCreditAmountError(CreditError):
    """A limit, amount, mode, date or note fails its rule."""


class CreditCustomerNotVerifiedError(CreditError):
    """The customer has not verified the phone, or the email when there is one."""


def _clean_amount(value: Decimal | int, *, field: str) -> Decimal:
    """Return ``value`` as a two-place Decimal, or refuse it.

    ``bool`` is a subclass of ``int``, so it is refused first: ``True`` must
    never become a money amount. Floats are refused for the same reason as in
    the inventory ledger: they cannot hold paise exactly.
    """
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise InvalidCreditAmountError(f"The {field} must be a decimal number.")
    amount = Decimal(value)
    if not amount.is_finite():
        raise InvalidCreditAmountError(f"The {field} must be a finite number.")
    if abs(amount) >= MAX_CREDIT_AMOUNT:
        raise InvalidCreditAmountError(f"The {field} is too large.")
    # ``quantize`` rounds to two places; a value that changes means it had
    # more than two decimals.
    if amount != amount.quantize(TWO_PLACES):
        raise InvalidCreditAmountError(f"The {field} must have at most two decimal places.")
    return amount.quantize(TWO_PLACES)


def _lock_user(user: User) -> User:
    """Lock and return the user row, the first lock in the fixed order (D67)."""
    return User.objects.select_for_update().get(pk=user.pk)


def _locked_account(user_id: int, *, require_active: bool) -> CreditAccount:
    """Lock and return the customer's account row, the last lock in the order."""
    account = CreditAccount.objects.select_for_update().filter(user_id=user_id).first()
    if account is None:
        raise CreditNotAvailableError("This customer has no credit account.")
    if require_active and not account.is_active:
        raise CreditNotAvailableError("This credit account is suspended.")
    return account


def set_credit_limit(user: User, limit: Decimal | int, *, actor: User | None) -> CreditAccount:
    """Set the limit, creating the account with a zero balance if it is missing."""
    value = _clean_amount(limit, field="credit limit")
    if value < 0:
        raise InvalidCreditAmountError("The credit limit cannot be negative.")
    with transaction.atomic():
        locked_user = _lock_user(user)
        # Credit is never given to an unverified customer (D71, D79 amendment).
        # Only a positive limit is refused; zero stays allowed, and an
        # existing account is never changed by this check.
        if value > ZERO and not locked_user.is_ready_for_delivery:
            raise CreditCustomerNotVerifiedError(
                "The customer must verify the phone number (and the email, if any) "
                "before getting credit."
            )
        account = CreditAccount.objects.select_for_update().filter(user=user).first()
        if account is None:
            account = CreditAccount.objects.create(user=user, credit_limit=value)
            # Creating an account changes the limit from an implicit zero.
            record_change(
                actor=actor,
                action=CREDIT_LIMIT_ACTION,
                target=account,
                old={"credit_limit": ZERO},
                new={"credit_limit": value},
                note="Credit limit (service)",
            )
            return account
        old = account.credit_limit
        if old != value:
            account.credit_limit = value
            account.save(update_fields=["credit_limit", "updated_at"])
            record_change(
                actor=actor,
                action=CREDIT_LIMIT_ACTION,
                target=account,
                old={"credit_limit": old},
                new={"credit_limit": value},
                note="Credit limit (service)",
            )
        return account


def set_credit_active(user: User, is_active: bool, *, actor: User | None) -> CreditAccount:
    """Suspend or re-enable credit. The account must already exist."""
    with transaction.atomic():
        _lock_user(user)
        account = CreditAccount.objects.select_for_update().filter(user=user).first()
        if account is None:
            raise CreditAccountNotFoundError("This customer has no credit account.")
        if account.is_active != is_active:
            old = account.is_active
            account.is_active = is_active
            account.save(update_fields=["is_active", "updated_at"])
            record_change(
                actor=actor,
                action=CREDIT_ACTIVE_ACTION,
                target=account,
                old={"is_active": old},
                new={"is_active": is_active},
                note="Credit account status (service)",
            )
        return account


def available_credit(user: User) -> Decimal:
    """What the customer may still charge: limit minus balance, never below zero."""
    account = CreditAccount.objects.filter(user=user).first()
    if account is None or not account.is_active:
        return ZERO
    # ``max`` returns one of its two arguments, so the result stays a Decimal.
    return max(account.credit_limit - account.balance, ZERO)


def _entry_date(now: datetime) -> date:
    """Today's date in the shop time zone, from an aware datetime."""
    return timezone.localdate(now)


def charge_order(
    order: Order,
    *,
    actor: User | None = None,
    now: datetime | None = None,
) -> CreditEntry:
    """Charge an order's khata part to its customer's credit account.

    The account is always found through the order's user, so a caller can
    never charge somebody else's account. ``Order.credit_applied`` is the
    amount, so a split order charges only what the customer chose to put on
    the khata (D74). A zero part raises ``InvalidCreditAmountError`` and writes
    nothing. Missing or suspended credit raises ``CreditNotAvailableError``; a
    part above the available credit raises ``CreditLimitExceededError`` and
    writes nothing. A repeated call returns the first ORDER entry and writes
    nothing (D67 point 10).
    """
    resolved_now = now if now is not None else timezone.now()
    with transaction.atomic():
        _lock_user(order.user)
        locked_order = Order.objects.select_for_update().get(pk=order.pk)
        account = _locked_account(locked_order.user_id, require_active=False)
        existing = CreditEntry.objects.filter(
            entry_type=CreditEntry.EntryType.ORDER,
            order=locked_order,
        ).first()
        if existing is not None:
            return existing
        amount = locked_order.credit_applied
        if amount <= ZERO:
            raise InvalidCreditAmountError("There is no credit amount to charge.")
        if not account.is_active:
            raise CreditNotAvailableError("This credit account is suspended.")

        available = max(account.credit_limit - account.balance, ZERO)
        if amount > available:
            raise CreditLimitExceededError(
                f"Available credit is {available}, below the order total {amount}."
            )

        balance_after = account.balance + amount
        entry = CreditEntry.objects.create(
            account=account,
            entry_type=CreditEntry.EntryType.ORDER,
            amount=amount,
            balance_after=balance_after,
            order=locked_order,
            entry_date=_entry_date(resolved_now),
            created_by=actor,
        )
        account.balance = balance_after
        account.save(update_fields=["balance", "updated_at"])
        return entry


def reverse_order_charge(
    order: Order,
    *,
    actor: User | None = None,
    now: datetime | None = None,
) -> CreditEntry | None:
    """Give an order's credit back when it is cancelled before delivery.

    An order that was never charged returns ``None`` and writes nothing. A
    repeated reversal returns the first one and writes nothing. The reversal
    reverses the stored charge amount, not ``order.grand_total`` again, so a
    damaged order total cannot make the two rows disagree.
    """
    resolved_now = now if now is not None else timezone.now()
    with transaction.atomic():
        _lock_user(order.user)
        locked_order = Order.objects.select_for_update().get(pk=order.pk)
        account = (
            CreditAccount.objects.select_for_update().filter(user_id=locked_order.user_id).first()
        )
        if account is None:
            return None
        charge = CreditEntry.objects.filter(
            entry_type=CreditEntry.EntryType.ORDER,
            order=locked_order,
        ).first()
        if charge is None:
            return None
        existing = CreditEntry.objects.filter(
            entry_type=CreditEntry.EntryType.CANCEL_REVERSAL,
            order=locked_order,
        ).first()
        if existing is not None:
            return existing

        amount = -charge.amount
        balance_after = account.balance + amount
        entry = CreditEntry.objects.create(
            account=account,
            entry_type=CreditEntry.EntryType.CANCEL_REVERSAL,
            amount=amount,
            balance_after=balance_after,
            order=locked_order,
            entry_date=_entry_date(resolved_now),
            created_by=actor,
        )
        account.balance = balance_after
        account.save(update_fields=["balance", "updated_at"])
        return entry


def record_payment(
    user: User,
    amount: Decimal | int,
    *,
    mode: str,
    entry_date: date,
    reference: str = "",
    note: str = "",
    actor: User | None,
    now: datetime | None = None,
) -> CreditEntry:
    """Record a payment received by staff; the stored amount is negative.

    The amount must be positive and no more than the current balance, so a
    typo cannot create a negative balance. A refund or an overpayment is an
    ``ADJUSTMENT`` instead (D67 point 8).
    """
    resolved_now = now if now is not None else timezone.now()
    value = _clean_amount(amount, field="payment amount")
    if value <= 0:
        raise InvalidCreditAmountError("A payment must be more than zero.")
    if mode not in CreditEntry.PaymentMode.values:
        raise InvalidCreditAmountError("Choose a payment mode.")
    # A datetime is a date subclass; keep only its date part for the field.
    received_on = entry_date.date() if isinstance(entry_date, datetime) else entry_date
    if received_on > _entry_date(resolved_now):
        raise InvalidCreditAmountError("The payment date cannot be in the future.")
    reference = (reference or "").strip()[:100]
    note = (note or "").strip()[:200]

    with transaction.atomic():
        _lock_user(user)
        # A payment is allowed on a suspended account: money owed is still
        # collected. Only a new charge needs active credit (D67 point 7).
        account = _locked_account(user.pk, require_active=False)
        if value > account.balance:
            raise CreditLimitExceededError(
                f"The payment is more than the outstanding balance {account.balance}."
            )
        balance_after = account.balance - value
        entry = CreditEntry.objects.create(
            account=account,
            entry_type=CreditEntry.EntryType.PAYMENT,
            amount=-value,
            balance_after=balance_after,
            payment_mode=mode,
            reference=reference,
            note=note,
            entry_date=received_on,
            created_by=actor,
        )
        account.balance = balance_after
        account.save(update_fields=["balance", "updated_at"])
        return entry


def record_adjustment(
    user: User,
    amount: Decimal | int,
    *,
    note: str,
    actor: User | None,
    now: datetime | None = None,
) -> CreditEntry:
    """Add a correction row. A positive amount raises the balance, a negative lowers it.

    An adjustment always needs a non-blank note, so the reason for it stays
    readable in the ledger (D67 points 2 and 8).
    """
    resolved_now = now if now is not None else timezone.now()
    value = _clean_amount(amount, field="adjustment amount")
    if value == 0:
        raise InvalidCreditAmountError("An adjustment cannot be zero.")
    cleaned_note = (note or "").strip()
    if not cleaned_note:
        raise InvalidCreditAmountError("An adjustment needs a note.")

    with transaction.atomic():
        _lock_user(user)
        account = _locked_account(user.pk, require_active=False)
        balance_after = account.balance + value
        entry = CreditEntry.objects.create(
            account=account,
            entry_type=CreditEntry.EntryType.ADJUSTMENT,
            amount=value,
            balance_after=balance_after,
            note=cleaned_note[:200],
            entry_date=_entry_date(resolved_now),
            created_by=actor,
        )
        account.balance = balance_after
        account.save(update_fields=["balance", "updated_at"])
        return entry
