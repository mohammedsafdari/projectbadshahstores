"""Read-only integrity check for the credit ledger (D67 points 4 and 10).

Nothing here writes or locks anything. The database already refuses zero
amounts, wrongly signed rows and duplicate charges. This module proves the two
rules only the services enforce: the cached account balance equals the entries,
and every ``balance_after`` follows the one before it. It also checks that an
ORDER entry still matches its order's frozen total and belongs to a credit
order, and that a live or cancelled credit order has the entries D68 requires.
"""

from dataclasses import dataclass
from decimal import Decimal

from apps.orders.models import Order

from .models import CreditAccount, CreditEntry


# A dataclass is a class for which Python writes __init__, __repr__ and ==
# from the field list. frozen=True makes an instance read-only.
@dataclass(frozen=True)
class CreditProblem:
    """One finding of check_credit_integrity."""

    account_id: int
    code: str
    detail: str


def check_credit_integrity() -> list[CreditProblem]:
    """Return every credit problem found. An empty list means the ledger is clean."""
    problems: list[CreditProblem] = []
    accounts = list(CreditAccount.objects.all().order_by("pk"))

    # One query for every entry, grouped in Python by the account it belongs
    # to. ``select_related("order")`` avoids a query per ORDER entry.
    entries_by_account: dict[int, list[CreditEntry]] = {}
    for entry in CreditEntry.objects.select_related("order").order_by("account_id", "id"):
        # ``setdefault`` creates the empty list on first use and returns it.
        entries_by_account.setdefault(entry.account_id, []).append(entry)

    # One charge entry and one reversal entry per order, read from the list we
    # already have, so the order checks below need no extra entry query.
    charges_by_order: dict[int, CreditEntry] = {}
    reversed_order_ids: set[int] = set()
    for entries in entries_by_account.values():
        for entry in entries:
            if entry.order_id is None:
                continue
            if entry.entry_type == CreditEntry.EntryType.ORDER:
                charges_by_order.setdefault(entry.order_id, entry)
            elif entry.entry_type == CreditEntry.EntryType.CANCEL_REVERSAL:
                reversed_order_ids.add(entry.order_id)

    for account in accounts:
        entries = entries_by_account.get(account.pk, [])
        if not entries:
            # An account that was never used must be clean at zero.
            if account.balance != Decimal("0.00"):
                problems.append(
                    CreditProblem(
                        account_id=account.pk,
                        code="balance_mismatch",
                        detail=(
                            f"Account {account.pk} has no entries but its balance is "
                            f"{account.balance}."
                        ),
                    )
                )
            continue

        # Sum in Python over the entries we already have, so each account is
        # read once. Decimal("0.00") is the starting value for the sum.
        total = sum((entry.amount for entry in entries), Decimal("0.00"))
        if total != account.balance:
            problems.append(
                CreditProblem(
                    account_id=account.pk,
                    code="balance_mismatch",
                    detail=(
                        f"Account {account.pk} has a cached balance of {account.balance} "
                        f"but its entries add up to {total}."
                    ),
                )
            )

        previous = Decimal("0.00")
        for entry in entries:
            if entry.balance_after != previous + entry.amount:
                problems.append(
                    CreditProblem(
                        account_id=account.pk,
                        code="chain_broken",
                        detail=(
                            f"Entry {entry.pk} has balance_after {entry.balance_after}, "
                            f"expected {previous + entry.amount}."
                        ),
                    )
                )
            previous = entry.balance_after

            if (
                entry.entry_type == CreditEntry.EntryType.ORDER
                and entry.order is not None
                and entry.amount != entry.order.grand_total
            ):
                problems.append(
                    CreditProblem(
                        account_id=account.pk,
                        code="order_amount_mismatch",
                        detail=(
                            f"Entry {entry.pk} charges {entry.amount} but order "
                            f"{entry.order_id} totals {entry.order.grand_total}."
                        ),
                    )
                )

    # A dictionary comprehension builds user id -> account id in one pass;
    # an order whose user has no account falls back to 0 (D68).
    account_id_by_user = {account.user_id: account.pk for account in accounts}

    # An ORDER entry must belong to a credit order, whatever its account says.
    for entry in charges_by_order.values():
        order = entry.order
        if order is not None and order.payment_method != Order.PaymentMethod.CREDIT:
            problems.append(
                CreditProblem(
                    account_id=entry.account_id,
                    code="charge_on_non_credit_order",
                    detail=(
                        f"Entry {entry.pk} charges order {entry.order_id}, "
                        f"which is a {order.payment_method} order."
                    ),
                )
            )

    # A live credit order needs one charge; a cancelled charged one needs the
    # reversal the cancel hook writes.
    for order in (
        Order.objects.filter(payment_method=Order.PaymentMethod.CREDIT)
        .select_related("user")
        .order_by("pk")
    ):
        account_id = account_id_by_user.get(order.user_id, 0)
        charged = order.pk in charges_by_order
        if order.status != Order.Status.CANCELLED and not charged:
            problems.append(
                CreditProblem(
                    account_id=account_id,
                    code="credit_order_without_charge",
                    detail=f"Order {order.pk} is a live credit order with no charge entry.",
                )
            )
        if (
            order.status == Order.Status.CANCELLED
            and charged
            and order.pk not in reversed_order_ids
        ):
            problems.append(
                CreditProblem(
                    account_id=account_id,
                    code="cancelled_credit_order_not_reversed",
                    detail=(
                        f"Cancelled credit order {order.pk} has a charge but no reversal entry."
                    ),
                )
            )

    return problems
