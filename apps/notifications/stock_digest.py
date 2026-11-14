"""The plain-text daily stock email: low stock, expiring and expired (D89).

No price and no customer detail appears here: the text goes to the shop's own
mailbox and nothing else. A section with nothing in it is left out.
"""

from __future__ import annotations

from decimal import Decimal

from apps.inventory.alerts import ExpiryRow, LowStockRow

# The event name the send log keeps for this message.
EVENT_STOCK_DIGEST = "STOCK_DIGEST"

# How many lines one section prints before it only counts the rest.
SECTION_LINES = 50

LOW_STOCK_TITLE = "Low stock"
EXPIRING_TITLE = "Expiring soon"
EXPIRED_TITLE = "Already expired"

# Both expiry sections carry this sentence, because a lot's amount is a guess
# about which purchase was sold first (D89).
ESTIMATE_NOTE = "estimate: assumes the oldest purchases are sold first"


def _quantity(value: Decimal) -> str:
    """A quantity without trailing zeros, so 2.000 prints as "2"."""
    # normalize() drops the trailing zeros; "f" keeps plain digits, never 1E+2.
    return format(value.normalize(), "f")


def _expiry_line(row: ExpiryRow) -> str:
    """One lot as one line: product, pack, SKU, batch, date and amount."""
    batch = row.batch_number or "-"
    return (
        f"{row.product} {row.variant} ({row.sku}): batch {batch}, "
        f"expires {row.expiry_date.isoformat()}, about {_quantity(row.remaining)} left"
    )


def _section(title: str, lines: list[str], note: str = "") -> list[str]:
    """One section: its heading, an optional note, its lines and the cut line."""
    block = [title]
    if note:
        block.append(note)
    for line in lines[:SECTION_LINES]:
        block.append(f"- {line}")
    if len(lines) > SECTION_LINES:
        block.append(f"...and {len(lines) - SECTION_LINES} more")
    return block


def build_stock_digest(
    low: list[LowStockRow],
    expiring: list[ExpiryRow],
    expired: list[ExpiryRow],
) -> tuple[str, str] | None:
    """The subject and the body of the daily email, or None when nothing (D89).

    ``None`` means every list was empty, so the job sends no email at all
    rather than a message that says "nothing to report".
    """
    if not low and not expiring and not expired:
        return None
    subject = (
        f"Badshah Stores stock alert: {len(low)} low, "
        f"{len(expiring)} expiring, {len(expired)} expired"
    )
    low_lines = [
        f"{row.product} {row.variant} ({row.sku}): {_quantity(row.available)} "
        f"{row.unit.lower()} left, threshold {_quantity(row.threshold)}"
        for row in low
    ]
    blocks: list[list[str]] = []
    if low_lines:
        blocks.append(_section(LOW_STOCK_TITLE, low_lines))
    if expiring:
        blocks.append(
            _section(EXPIRING_TITLE, [_expiry_line(row) for row in expiring], ESTIMATE_NOTE)
        )
    if expired:
        blocks.append(
            _section(EXPIRED_TITLE, [_expiry_line(row) for row in expired], ESTIMATE_NOTE)
        )
    # One blank line between the sections, and no trailing blank line.
    body = "\n\n".join("\n".join(block) for block in blocks)
    return subject, body
