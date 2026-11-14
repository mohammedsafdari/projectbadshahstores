"""Entering real cost prices from a private CSV (D85).

A cost price is our margin, so it is never committed to this public repository:
every seed file leaves ``ProductVariant.cost_price`` blank, and the real rates
come in through this upload, which staff only open with the
``MANAGE_COST_PRICES`` capability (D75).

The upload writes **one** field: ``ProductVariant.cost_price``, matched on
``sku_code``. It never creates a variant and never touches a price, an MRP, a
stock movement or any other field. Three steps, the same shape as the Slice 20
catalogue import (D47):

1. **parse** (``parse_cost_csv``): bytes in, checked rows out, plus every problem
   the file itself shows. No database read.
2. **plan** (``plan_cost_prices``): compare the rows with the variants and decide
   UPDATE, UNCHANGED or refuse. Writes nothing.
3. **apply** (``apply_cost_prices``): lock the variants, plan again and write in
   one transaction, with one audit row per changed cost.

The CSV safety rules are D47's: UTF-8 with an optional BOM, a formula-character
guard, a size limit, a row limit and plain decimal numbers only.

REVIEW CAREFULLY: this module writes our purchase rates.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.functions import Lower

from apps.accounts.capabilities import Capability, has_capability
from apps.accounts.models import User
from apps.catalog.csv_import import MAX_DATA_ROWS, MAX_FILE_BYTES, Issue
from apps.catalog.models import ProductVariant
from apps.core.audit import record_change

# The two columns the file must carry. Any other column is ignored, so a full
# catalogue export can be uploaded once its cost column is filled in.
SKU_COLUMN = "sku_code"
COST_COLUMN = "cost_price"
REQUIRED_COLUMNS = (SKU_COLUMN, COST_COLUMN)

# The two row answers of the plan. A refused row is not in the plan at all; it
# is reported as an issue instead.
UPDATE = "UPDATE"
UNCHANGED = "UNCHANGED"

COST_PRICE_UPDATE_ACTION = "COST_PRICE_UPDATE"

# Limits copied from the variant's ``cost_price`` column: 10 digits, 2 places.
MAX_MONEY_DIGITS = 10
MAX_COST_TEXT = 30
MONEY_PLACES = Decimal("0.01")
MIN_COST = Decimal("0")

# Excel and Google Sheets treat a cell that starts with one of these as a formula.
FORMULA_STARTERS = ("=", "+", "-", "@", "\t", "\r")

# re.ASCII matters: without it, \d also matches digits of other scripts.
_PLAIN_NUMBER_RE = re.compile(r"-?\d+(\.\d+)?", re.ASCII)
_SCIENTIFIC_RE = re.compile(r"[-+]?\d+(\.\d+)?[eE][-+]?\d+", re.ASCII)

PERMISSION_MESSAGE = "You do not have the right to enter cost prices."


class CostPriceError(ValueError):
    """Base error for every cost price refusal."""


class CostPriceRejected(CostPriceError):
    """The upload refuses the file and wrote nothing; ``issues`` says why."""

    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        super().__init__(f"The cost price upload was refused: {len(issues)} problem(s).")


# ---------------------------------------------------------------------------
# The parsed file
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CostRow:
    """One data row: a SKU and the cost the file asks for (None means "no change")."""

    line: int
    sku_code: str
    cost_price: Decimal | None


@dataclass
class CostParseResult:
    """The clean rows and every problem the file itself shows."""

    sha256: str = ""
    rows: list[CostRow] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    row_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.issues


def _shown(value: str, limit: int = 40) -> str:
    """Quote a cell for a message, cut short so a huge cell cannot flood the report."""
    text = value if len(value) <= limit else value[: limit - 1] + "…"
    return f"'{text}'"


def _unguard(value: str) -> str:
    """Undo the export's formula guard (D46 point 5).

    One leading quote is removed, but only when the character after it is a
    formula starter, so a name that really starts with a quote is left alone.
    """
    if len(value) > 1 and value[0] == "'" and value[1] in FORMULA_STARTERS:
        return value[1:]
    return value


def _has_control_character(value: str) -> bool:
    """True when the text holds a control character (NUL, bell, escape, ...)."""
    return any(unicodedata.category(char) == "Cc" for char in value)


def parse_cost_csv(raw: bytes) -> CostParseResult:
    """Read the uploaded file and return the clean rows and every problem.

    This function never raises for a bad file: every problem comes back in
    ``issues``, with line 0 for a problem with the whole file. It reads no
    database row.
    """
    result = CostParseResult(sha256=hashlib.sha256(raw).hexdigest())

    def fail(message: str) -> CostParseResult:
        result.issues.append(Issue(0, "", message))
        return result

    if len(raw) > MAX_FILE_BYTES:
        return fail(f"The file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    if not raw.strip():
        return fail("The file is empty.")
    try:
        # "utf-8-sig" is UTF-8 that also removes the byte order mark Excel adds.
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return fail(
            "The file is not UTF-8. In Excel choose 'CSV UTF-8 (comma delimited)' when saving."
        )
    if "\x00" in text:
        return fail("The file contains NUL characters. It is not a text CSV file.")

    # newline="" lets the csv module handle line breaks inside quoted cells.
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=",", strict=True)
    try:
        header = [name.strip() for name in next(reader)]
    except (StopIteration, csv.Error) as exc:
        return fail(f"The header line cannot be read: {exc}")

    repeated = sorted({name for name in header if header.count(name) > 1})
    if repeated:
        result.issues.append(Issue(1, "", f"Repeated columns: {', '.join(repeated)}."))
    missing = [name for name in REQUIRED_COLUMNS if name not in header]
    if missing:
        result.issues.append(
            Issue(
                1,
                "",
                f"Missing columns: {', '.join(missing)}. The file needs a "
                "'sku_code' column and a 'cost_price' column.",
            )
        )
    if result.issues:
        return result

    # Look each column up by name once, so the row loop does not search the
    # header again for every line.
    sku_index = header.index(SKU_COLUMN)
    cost_index = header.index(COST_COLUMN)
    seen: dict[str, int] = {}
    previous_end = reader.line_num
    while True:
        try:
            cells = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            result.issues.append(
                Issue(previous_end + 1, "", f"The file cannot be read here: {exc}")
            )
            return result
        start_line = previous_end + 1
        previous_end = reader.line_num
        if all(not cell.strip() for cell in cells):
            continue
        result.row_count += 1
        if result.row_count > MAX_DATA_ROWS:
            return fail(
                f"The file has more than {MAX_DATA_ROWS} data rows. Split it into smaller files."
            )
        if len(cells) != len(header):
            result.issues.append(
                Issue(
                    start_line,
                    "",
                    f"The row has {len(cells)} cells but the header has {len(header)}. "
                    "Check for a stray comma or an unclosed quote.",
                )
            )
            continue

        sku = _unguard(cells[sku_index].strip())
        if not sku:
            result.issues.append(Issue(start_line, SKU_COLUMN, "Required, but empty."))
            continue
        if _has_control_character(sku):
            result.issues.append(
                Issue(start_line, SKU_COLUMN, "Contains a control character. Remove it.")
            )
            continue
        if len(sku) > 40:
            result.issues.append(
                Issue(start_line, SKU_COLUMN, f"Too long: {len(sku)} characters, the limit is 40.")
            )
            continue
        if sku in seen:
            result.issues.append(
                Issue(
                    start_line,
                    SKU_COLUMN,
                    f"SKU {_shown(sku)} already appears on line {seen[sku]}.",
                )
            )
            continue
        seen[sku] = start_line

        raw_cost = _unguard(cells[cost_index].strip())
        if not raw_cost:
            # A blank cell means "leave this variant alone"; it is never zero (D85).
            result.rows.append(CostRow(line=start_line, sku_code=sku, cost_price=None))
            continue
        cost, problem = _parse_cost(raw_cost)
        if problem is not None:
            result.issues.append(Issue(start_line, COST_COLUMN, problem))
            continue
        result.rows.append(CostRow(line=start_line, sku_code=sku, cost_price=cost))

    if result.row_count == 0 and not result.issues:
        return fail("The file has a header but no data rows.")
    return result


def _parse_cost(text: str) -> tuple[Decimal | None, str | None]:
    """One cost cell as ``Decimal``, or the sentence that refuses it."""
    if len(text) > MAX_COST_TEXT:
        return None, "Too long to be a cost price."
    if "," in text:
        return None, (
            f"{_shown(text)} contains a comma. Use a dot as the decimal point, for example 82.50."
        )
    if _SCIENTIFIC_RE.fullmatch(text):
        return None, f"{_shown(text)} is in scientific notation. Write the plain number."
    if not _PLAIN_NUMBER_RE.fullmatch(text):
        return None, f"{_shown(text)} is not a number. Use digits and a dot, for example 82.50."
    try:
        value = Decimal(text)
    except InvalidOperation:  # cannot happen after the pattern check; kept as a guard
        return None, f"{_shown(text)} is not a number."
    rounded = value.quantize(MONEY_PLACES)
    if rounded != value:
        return None, f"{_shown(text)} has more than 2 decimal places."
    if len(rounded.as_tuple().digits) > MAX_MONEY_DIGITS:
        return None, f"{_shown(text)} is too large for a cost price."
    if rounded < MIN_COST:
        return None, f"{_shown(text)} must be at least 0."
    return rounded, None


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CostRowPlan:
    """What would happen to one row of the file."""

    line: int
    sku_code: str
    action: str
    variant_pk: int | None
    stored_cost: Decimal | None
    new_cost: Decimal | None
    variant_updated_at: datetime | None
    warning: str


@dataclass(frozen=True)
class CostPricePlan:
    """Every row's answer, plus the problems that stop the file being applied."""

    sha256: str
    row_count: int
    rows: list[CostRowPlan]
    issues: list[Issue]

    @property
    def ok(self) -> bool:
        return not self.issues

    def counts(self) -> dict[str, int]:
        """How many rows are UPDATE and UNCHANGED."""
        totals = {UPDATE: 0, UNCHANGED: 0}
        for row in self.rows:
            totals[row.action] += 1
        return totals

    def warned(self) -> list[CostRowPlan]:
        """The rows whose cost is above the selling price: a warning, not a refusal."""
        return [row for row in self.rows if row.warning]

    def fingerprint(self) -> str:
        """A short text that changes when the file or the stored costs change.

        The apply step compares it, so a preview made before somebody else's
        edit is never applied blindly (the same idea as D47 point 5).
        """
        payload: list[Any] = [
            self.sha256,
            [
                [
                    row.line,
                    row.sku_code,
                    row.action,
                    row.variant_pk,
                    None if row.variant_updated_at is None else row.variant_updated_at.isoformat(),
                    None if row.new_cost is None else str(row.new_cost),
                ]
                for row in sorted(self.rows, key=lambda row: (row.line, row.sku_code))
            ],
        ]
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require_writer(actor: User | None) -> None:
    """Only a staff member with ``MANAGE_COST_PRICES`` may read or write a cost."""
    if not isinstance(actor, User) or not has_capability(actor, Capability.MANAGE_COST_PRICES):
        raise CostPriceError(PERMISSION_MESSAGE)


def _stored_variants(skus: set[str], *, for_update: bool) -> dict[str, ProductVariant]:
    """The variants the file mentions, one query, optionally locked by ``sku_code``.

    ``Lower`` lets one query find a SKU that differs only in letter case, so the
    plan can refuse it with a clear message instead of calling it unknown.
    """
    queryset = ProductVariant.objects.select_related("product")
    if for_update:
        queryset = queryset.select_for_update()
    rows = queryset.annotate(sku_lower=Lower("sku_code")).filter(
        sku_lower__in={sku.lower() for sku in skus}
    )
    by_lower: dict[str, ProductVariant] = {}
    for variant in rows.order_by("pk"):
        by_lower.setdefault(variant.sku_code.lower(), variant)
    return by_lower


def _build_plan(parsed: CostParseResult, stored: dict[str, ProductVariant]) -> CostPricePlan:
    """Decide UPDATE, UNCHANGED or refuse for every clean row."""
    issues: list[Issue] = list(parsed.issues)
    rows: list[CostRowPlan] = []
    for row in parsed.rows:
        variant = stored.get(row.sku_code.lower())
        if variant is None:
            issues.append(
                Issue(
                    row.line,
                    SKU_COLUMN,
                    f"Unknown SKU {_shown(row.sku_code)}. This upload never creates a variant.",
                )
            )
            continue
        if variant.sku_code != row.sku_code:
            issues.append(
                Issue(
                    row.line,
                    SKU_COLUMN,
                    f"SKU {_shown(row.sku_code)} differs only in letter case from the stored "
                    f"SKU {_shown(variant.sku_code)}.",
                )
            )
            continue
        new_cost = row.cost_price
        if new_cost is None or new_cost == variant.cost_price:
            rows.append(
                CostRowPlan(
                    line=row.line,
                    sku_code=variant.sku_code,
                    action=UNCHANGED,
                    variant_pk=variant.pk,
                    stored_cost=variant.cost_price,
                    new_cost=new_cost,
                    variant_updated_at=variant.updated_at,
                    warning="",
                )
            )
            continue
        warning = ""
        if new_cost > variant.selling_price:
            warning = f"The cost {new_cost} is above the selling price {variant.selling_price}."
        rows.append(
            CostRowPlan(
                line=row.line,
                sku_code=variant.sku_code,
                action=UPDATE,
                variant_pk=variant.pk,
                stored_cost=variant.cost_price,
                new_cost=new_cost,
                variant_updated_at=variant.updated_at,
                warning=warning,
            )
        )
    return CostPricePlan(
        sha256=parsed.sha256,
        row_count=parsed.row_count,
        rows=rows,
        issues=sorted(issues, key=lambda issue: (issue.line, issue.column)),
    )


def plan_cost_prices(raw: bytes, *, actor: User | None) -> CostPricePlan:
    """Compare the file with the stored costs and write nothing."""
    _require_writer(actor)
    parsed = parse_cost_csv(raw)
    stored = _stored_variants({row.sku_code for row in parsed.rows}, for_update=False)
    return _build_plan(parsed, stored)


# ---------------------------------------------------------------------------
# The apply
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CostApplyResult:
    """What one successful upload did."""

    updated: int
    unchanged: int
    warnings: int
    sha256: str


def _note(filename: str) -> str:
    """The audit note: ``Cost price CSV: <file name>``, control characters removed."""
    cleaned = "".join(char for char in filename if unicodedata.category(char) != "Cc")
    return ("Cost price CSV: " + cleaned).strip()[:200]


def _error_text(exc: Exception) -> str:
    """One short line from a validation or database error, for the report."""
    text = " ".join(str(exc).split())
    if len(text) > 200:
        return text[:197] + "..."
    return text


def apply_cost_prices(
    raw: bytes,
    *,
    actor: User | None,
    filename: str,
    expected_fingerprint: str | None = None,
) -> CostApplyResult:
    """Write the cost prices the file asks for, or nothing at all.

    ``actor`` goes into every audit row. ``expected_fingerprint`` is the text the
    preview showed; when it is given and the fresh plan differs, nothing is
    written and the person uploads the file again.
    """
    _require_writer(actor)
    parsed = parse_cost_csv(raw)
    if not parsed.ok and not parsed.rows and any(issue.line == 0 for issue in parsed.issues):
        raise CostPriceRejected(parsed.issues)

    # The try is OUTSIDE atomic on purpose: catching a database error inside the
    # block would leave the transaction broken.
    try:
        with transaction.atomic():
            return _apply_locked(
                parsed,
                actor=actor,
                note=_note(filename),
                expected_fingerprint=expected_fingerprint,
            )
    except (ValidationError, IntegrityError) as exc:
        raise CostPriceRejected([Issue(0, "", _error_text(exc))]) from exc


def _apply_locked(
    parsed: CostParseResult,
    *,
    actor: User | None,
    note: str,
    expected_fingerprint: str | None,
) -> CostApplyResult:
    """Do the work with the variant rows locked. Call it inside ``atomic``."""
    stored = _stored_variants({row.sku_code for row in parsed.rows}, for_update=True)
    # Plan again with the locks held: another upload may have committed while
    # this request waited for its lock.
    plan = _build_plan(parsed, stored)
    if not plan.ok:
        raise CostPriceRejected(plan.issues)
    if expected_fingerprint is not None and expected_fingerprint != plan.fingerprint():
        raise CostPriceRejected(
            [Issue(0, "", "The data changed since the preview. Upload the file again.")]
        )

    updated = 0
    unchanged = 0
    for row in plan.rows:
        if row.action != UPDATE or row.new_cost is None:
            unchanged += 1
            continue
        variant = stored[row.sku_code.lower()]
        old_cost = variant.cost_price
        variant.cost_price = row.new_cost
        variant.full_clean()
        # update_fields writes only the named columns, so updated_at must be in
        # the list: with update_fields Django does not add auto_now fields.
        variant.save(update_fields=["cost_price", "updated_at"])
        record_change(
            actor=actor,
            action=COST_PRICE_UPDATE_ACTION,
            target=variant,
            old={"cost_price": old_cost},
            new={"cost_price": row.new_cost},
            note=note,
        )
        updated += 1

    return CostApplyResult(
        updated=updated,
        unchanged=unchanged,
        warnings=len(plan.warned()),
        sha256=plan.sha256,
    )
