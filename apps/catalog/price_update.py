"""Bulk price update: plan every change first, then write them in one transaction.

Slice 21. The admin action on the variant list calls the two functions here:

1. ``plan_price_update`` reads the selected variants with one query and works out the
   new price of each one. It writes nothing, so the admin can show the plan as a
   preview.
2. ``apply_price_update`` locks the same rows, plans again under the lock, compares
   the fingerprint the person saw, and then writes every row or none of them.

The arithmetic lives only here, so the preview and the write cannot disagree.

Rules (D44, D47 and the Slice 21 decisions):

- one field per run: ``selling_price``, ``mrp`` or ``cost_price``;
- one method per run: a percentage change, an amount change or an exact value;
- the result is rounded half-up to a step of 1, 0.50 or 0.01; "set exact" is not
  rounded;
- a row with a problem stops the whole run, a row whose ``cost_price`` is blank under
  a percent or amount change is skipped, and a row that does not change is not listed;
- every write takes ``snapshot_variant_from_db`` before and ``record_variant_change``
  after, and uses ``save(update_fields=[...])``. ``queryset.update()`` is never used:
  it would skip both the audit row and ``updated_at``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.contrib.auth.base_user import AbstractBaseUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.catalog.audit import record_variant_change, snapshot_variant_from_db
from apps.catalog.models import ProductVariant

# ---------------------------------------------------------------------------
# The words the admin form and the tests use. Plain strings, so nothing has to
# import an enum to compare them.
# ---------------------------------------------------------------------------
FIELDS: tuple[str, ...] = ("selling_price", "mrp", "cost_price")

PERCENT = "percent"
AMOUNT = "amount"
SET = "set"
METHODS: tuple[str, ...] = (PERCENT, AMOUNT, SET)

# Decimals, never floats: the step is money and must stay exact. ``0.50`` and
# ``0.5`` compare equal, so either spelling from a form is accepted.
DEFAULT_STEP: Decimal = Decimal("1")
STEPS: tuple[Decimal, ...] = (DEFAULT_STEP, Decimal("0.50"), Decimal("0.01"))

# One run is a person reading a preview, so it stays small. The apply step locks
# every row of the run at once.
MAX_VARIANTS = 1000

# ``max_digits=10, decimal_places=2`` on the model leaves eight digits before the
# decimal point, so the largest price that fits is 99,999,999.99.
_MAX_PRICE = Decimal("100000000")


# ---------------------------------------------------------------------------
# Results
# ``frozen=True`` makes these objects read-only, so a plan someone is about to
# show cannot be edited afterwards by accident.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PriceSpec:
    """What the person asked for: one field, one method, one value and one step."""

    field: str  # "selling_price", "mrp" or "cost_price"
    method: str  # "percent", "amount" or "set"
    value: Decimal
    # One rupee unless the person picked 0.50 or 0.01. The default is a module-level
    # value, not a call here, which is what ruff asks for in a dataclass default.
    step: Decimal = DEFAULT_STEP

    def describe(self) -> str:
        """A short line for the preview, the page title and the audit note."""
        label = self.field.replace("_", " ")
        if self.method == SET:
            # "Set exact" is not rounded, so the step is not worth mentioning.
            return f"{label} set to {_plain(self.value)}"
        rounded = f" (round to {_plain(self.step)})"
        if self.method == PERCENT:
            return f"{label} {_signed(self.value)}%{rounded}"
        return f"{label} {_signed(self.value)} rupees{rounded}"


@dataclass(frozen=True)
class PriceRow:
    """One variant whose price would change, with both sides of the change."""

    variant_pk: int
    sku_code: str
    # None only when a blank cost price is being set for the first time.
    old: Decimal | None
    new: Decimal
    # When the row was read. The fingerprint includes it, so an edit made after the
    # preview changes the fingerprint and Confirm refuses to write.
    updated_at: datetime | None = None


@dataclass(frozen=True)
class Skipped:
    """A selected variant that is left alone, with the reason."""

    sku_code: str
    reason: str


@dataclass(frozen=True)
class Problem:
    """Something that stops the whole run. ``sku_code`` is "" for a spec problem."""

    sku_code: str
    message: str


@dataclass(frozen=True)
class PricePlan:
    """The whole answer: what would change, what is skipped and what is wrong."""

    spec: PriceSpec
    rows: list[PriceRow]
    skipped: list[Skipped]
    problems: list[Problem]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        """True when nothing blocks the run: every row can be written."""
        return not self.problems

    def fingerprint(self) -> str:
        """A short text that changes when the spec or any changed row changes.

        It covers the spec plus each row's ``pk``, ``updated_at``, old and new
        price. The same request against untouched rows gives the same text; an
        edit to one of those rows in between (which moves its ``updated_at``)
        gives a different one, so Confirm never applies an old preview.
        """
        spec = self.spec
        payload = [
            [spec.field, spec.method, _plain(spec.value), _plain(spec.step)],
            [
                [
                    row.variant_pk,
                    _iso(row.updated_at),
                    None if row.old is None else _plain(row.old),
                    _plain(row.new),
                ]
                for row in self.rows
            ],
        ]
        # sort_keys and the compact separators make the JSON text identical on every
        # run, so the hash only changes when the content does.
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


class PriceUpdateRejected(Exception):
    """Raised when the update refuses to run. Nothing was written.

    ``problems`` has the same shape the preview uses, so the page can show the
    reason with the code it already has.
    """

    def __init__(self, problems: list[Problem]) -> None:
        self.problems = problems
        super().__init__(f"The price update was refused: {len(problems)} problem(s).")


class PlanChanged(PriceUpdateRejected):
    """Raised when the prices changed between the preview and the click on Confirm."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _plain(value: Decimal) -> str:
    """Digits only, no exponent and no trailing zeros: ``Decimal("5.00")`` -> ``"5"``.

    ``format(value, "f")`` is the "fixed point" presentation, so a value stored
    as ``1E+2`` comes out as ``100`` instead of the exponent form Python would
    print by default.
    """
    text = format(value, "f")
    # Only strip a fractional part; stripping "100" would leave "1".
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _signed(value: Decimal) -> str:
    """A value with an explicit sign, so a change always reads ``+5`` or ``-5``."""
    text = _plain(value)
    return text if text.startswith("-") else "+" + text


def _decimal_places(value: Decimal) -> int:
    """How many digits sit after the decimal point: 2 for 12.50, 0 for 12."""
    exponent = value.as_tuple().exponent
    # The exponent is an int for a normal number and a str ("n", "F") for NaN or
    # Infinity, which the spec check rejects separately.
    if isinstance(exponent, int) and exponent < 0:
        return -exponent
    return 0


def _two_places(value: Decimal) -> Decimal:
    """Fill in the second decimal place without rounding: ``5`` -> ``5.00``."""
    try:
        return value.quantize(Decimal("0.01"))
    except InvalidOperation:
        # More digits than the decimal context (28) can hold. No stored price is
        # that large, so hand the number back unchanged and let the range check
        # in _row_problem report it.
        return value


def _round_to_step(raw: Decimal, step: Decimal) -> Decimal:
    """Round ``raw`` half-up to the nearest multiple of ``step``, with 2 decimals."""
    try:
        # Count how many steps fit in the raw value, round that count half-up
        # (10.5 steps becomes 11, which is what a shopkeeper expects), and
        # multiply back to get a price again.
        steps = (raw / step).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        # The result is an exact multiple of at least 0.01, so this quantize only
        # fixes the number of decimal places (10.0 -> 10.00); it never rounds.
        return (steps * step).quantize(Decimal("0.01"))
    except InvalidOperation:
        # Same reason as _two_places: an absurd value, reported by the range check.
        return raw


def _new_value(old: Decimal, spec: PriceSpec) -> Decimal:
    """The new price for one stored price, computed and rounded as the spec asks."""
    if spec.method == PERCENT:
        # ``Decimal(1) + ...`` keeps the whole sum a Decimal; adding a float would
        # turn the price into a float and lose exactness.
        raw = old * (Decimal(1) + spec.value / Decimal(100))
    elif spec.method == AMOUNT:
        raw = old + spec.value
    else:
        # "Set exact" is not rounded at all.
        return _two_places(spec.value)
    return _round_to_step(raw, spec.step)


def _iso(value: datetime | None) -> str | None:
    """ISO text for a timestamp, or None when there is none. JSON has no datetime."""
    return None if value is None else value.isoformat()


def _error_text(exc: Exception) -> str:
    """One short line from a validation or database error, for the report."""
    text = " ".join(str(exc).split())
    if len(text) > 200:
        return text[:197] + "..."
    return text


def _note(spec: PriceSpec) -> str:
    """The audit note: ``Bulk price update: <short description>``, cut to 200."""
    return ("Bulk price update: " + spec.describe())[:200]


# ---------------------------------------------------------------------------
# Checking the request
# ---------------------------------------------------------------------------
def _spec_problems(spec: PriceSpec) -> list[Problem]:
    """Everything wrong with the request itself, before any variant is read."""
    problems: list[Problem] = []
    if spec.field not in FIELDS:
        problems.append(Problem("", f"Unknown field '{spec.field}'."))
    if spec.method not in METHODS:
        problems.append(Problem("", f"Unknown method '{spec.method}'."))
    if spec.step not in STEPS:
        problems.append(Problem("", "The rounding step must be 1, 0.50 or 0.01."))
    if not spec.value.is_finite():
        # NaN and Infinity break every comparison and every sum below.
        problems.append(Problem("", "The value must be a normal number."))
        return problems
    if _decimal_places(spec.value) > 2:
        problems.append(Problem("", "The value must have at most 2 decimal places."))
    if spec.method == PERCENT and spec.value <= -100:
        problems.append(
            Problem("", "A percentage of -100 or lower would make every price zero or negative.")
        )
    return problems


def _range_problem(label: str, new: Decimal) -> str | None:
    """The rule about the model's digits, or None when the new price fits."""
    if abs(new) >= _MAX_PRICE:
        return (
            f"The new {label} ({_plain(new)}) does not fit 10 digits with 2 decimals "
            "(at most 99,999,999.99)."
        )
    return None


def _row_problem(variant: ProductVariant, spec: PriceSpec, new: Decimal) -> str | None:
    """The rule that stops this row, or None when the new price is allowed."""
    label = spec.field.replace("_", " ")
    if spec.field == "cost_price":
        if new < 0:
            return "The new cost price must be 0 or more."
    elif new <= 0:
        return f"The new {label} must be more than 0."

    problem = _range_problem(label, new)
    if problem is not None:
        return problem

    # The other money field keeps its stored value, so the two must still agree
    # (the database has the same rule as a check constraint).
    if spec.field == "selling_price" and new > variant.mrp:
        return (
            f"The new selling price ({_plain(new)}) is above the stored MRP "
            f"({_plain(variant.mrp)}). Change the MRP first."
        )
    if spec.field == "mrp" and variant.selling_price > new:
        return (
            f"The new MRP ({_plain(new)}) is below the stored selling price "
            f"({_plain(variant.selling_price)}). Change the selling price first."
        )
    return None


def _warning(sku_code: str, old: Decimal, new: Decimal) -> str | None:
    """A note (not a block) when one row moves by more than 50 percent."""
    if old == 0:
        # A ratio needs a non-zero old value; any move away from 0 is large.
        if new == 0:
            return None
        return f"{sku_code}: {_plain(old)} to {_plain(new)} is more than a 50% change."
    percent = (new - old) / old * Decimal(100)
    if abs(percent) <= 50:
        return None
    shown = _signed(percent.quantize(Decimal("0.1")))
    return f"{sku_code}: {_plain(old)} to {_plain(new)} is {shown}%."


# ---------------------------------------------------------------------------
# Building a plan from rows that are already read
# ---------------------------------------------------------------------------
def _build_plan(variants: list[ProductVariant], spec: PriceSpec) -> PricePlan:
    """Work out every row from variant instances the caller already fetched.

    ``plan_price_update`` passes rows from a plain query and ``apply_price_update``
    passes the same rows locked with ``select_for_update()``, so both use exactly
    this arithmetic.
    """
    rows: list[PriceRow] = []
    skipped: list[Skipped] = []
    problems: list[Problem] = []
    warnings: list[str] = []

    # Sorted by pk so the plan (and its fingerprint) is the same however the rows
    # arrived, and so two overlapping runs walk the rows in the same order (D43).
    for variant in sorted(variants, key=lambda row: row.pk):
        # ``getattr`` reads a field whose name is only known at runtime: the spec
        # says which of the three fields this run changes.
        old = getattr(variant, spec.field)

        if old is None:
            # Only cost_price may be NULL. A percent or amount change needs a
            # starting number; "set exact" simply fills the blank in.
            if spec.method != SET:
                skipped.append(
                    Skipped(
                        variant.sku_code,
                        f"The cost price is blank, so a {spec.method} change cannot be worked out.",
                    )
                )
                continue
            new = _two_places(spec.value)
        else:
            new = _new_value(old, spec)

        message = _row_problem(variant, spec, new)
        if message is not None:
            problems.append(Problem(variant.sku_code, message))
            continue

        if new == old:
            # Nothing to write, so this row is neither a change nor a skip.
            continue

        if old is not None:
            warning = _warning(variant.sku_code, old, new)
            if warning is not None:
                warnings.append(warning)

        rows.append(
            PriceRow(
                variant_pk=variant.pk,
                sku_code=variant.sku_code,
                old=old,
                new=new,
                updated_at=variant.updated_at,
            )
        )

    return PricePlan(
        spec=spec,
        rows=rows,
        skipped=skipped,
        problems=problems,
        warnings=warnings,
    )


def _pks_and_problems(
    variant_pks: Iterable[int], spec: PriceSpec
) -> tuple[list[int], list[Problem]]:
    """The selection as sorted unique ids, plus everything that refuses the run.

    ``set()`` removes a repeated id (a row must not be written twice) and ``sorted``
    keeps the order stable for the locks and the fingerprint.
    """
    pks = sorted(set(variant_pks))
    problems = _spec_problems(spec)
    if len(pks) > MAX_VARIANTS:
        problems.append(Problem("", f"Select at most {MAX_VARIANTS} variants per run."))
    return pks, problems


# ---------------------------------------------------------------------------
# Step one: the preview
# ---------------------------------------------------------------------------
def plan_price_update(variant_pks: Iterable[int], spec: PriceSpec) -> PricePlan:
    """Work out every change without writing anything.

    Reads the selected variants with one query. An invalid spec or more than
    ``MAX_VARIANTS`` ids are refused before that query is even made.
    """
    pks, problems = _pks_and_problems(variant_pks, spec)
    if problems:
        return PricePlan(
            spec=spec,
            rows=[],
            skipped=[],
            problems=problems,
            warnings=[],
        )

    # One query for the whole run, in ``pk`` order, with no ``related`` loading:
    # the plan only needs the fields of the variant row itself.
    variants = list(ProductVariant.objects.filter(pk__in=pks).order_by("pk"))
    return _build_plan(variants, spec)


# ---------------------------------------------------------------------------
# Step two: the write
# ---------------------------------------------------------------------------
def apply_price_update(
    variant_pks: Iterable[int],
    spec: PriceSpec,
    *,
    actor: AbstractBaseUser | None,
    expected_fingerprint: str | None = None,
) -> int:
    """Change the price on every selected variant, or on none of them.

    Returns how many variants changed. ``actor`` goes into every audit row (None
    for a system run). ``expected_fingerprint`` is the fingerprint the person saw
    on the preview; when it is given and the fresh plan differs, nothing is written.
    """
    pks, problems = _pks_and_problems(variant_pks, spec)
    if problems:
        raise PriceUpdateRejected(problems)

    try:
        # The try is OUTSIDE atomic on purpose: catching a database error inside the
        # block leaves the transaction marked as broken, so every later query would
        # fail. After the block exits, the transaction has already rolled back.
        with transaction.atomic():
            return _apply_locked(pks, spec, actor=actor, expected_fingerprint=expected_fingerprint)
    except (ValidationError, IntegrityError) as exc:
        raise PriceUpdateRejected([Problem("", _error_text(exc))]) from exc


def _apply_locked(
    pks: list[int],
    spec: PriceSpec,
    *,
    actor: AbstractBaseUser | None,
    expected_fingerprint: str | None,
) -> int:
    """Do the work with the locks held. Call it inside ``transaction.atomic()``.

    ``list()`` matters because a queryset is lazy: the lock is taken when the rows
    are actually read, not when the queryset is built. One ``pk`` order means two
    runs over overlapping selections queue instead of deadlocking (D43, D47).
    """
    locked = list(ProductVariant.objects.select_for_update().filter(pk__in=pks).order_by("pk"))

    # Plan again now that nobody else can change these rows: the preview may be
    # minutes old, and the plan that gets written must describe the stored data.
    plan = _build_plan(locked, spec)
    if not plan.ok:
        raise PriceUpdateRejected(plan.problems)
    if expected_fingerprint is not None and plan.fingerprint() != expected_fingerprint:
        raise PlanChanged(
            [Problem("", "The prices changed since the preview. Look at them again.")]
        )

    by_pk = {variant.pk: variant for variant in locked}
    note = _note(spec)
    changed = 0
    for row in plan.rows:
        variant = by_pk[row.variant_pk]
        # Take the old values BEFORE touching the instance: the audit row needs
        # both sides of the change, and the instance is about to hold the new one.
        old = snapshot_variant_from_db(variant.pk)
        setattr(variant, spec.field, row.new)
        # full_clean() runs the field validators and the check constraints
        # (price above 0, selling price at or below MRP, ...) before any write.
        variant.full_clean()
        # update_fields writes only the named columns, so updated_at must be in
        # the list: with update_fields Django does not add auto_now fields itself.
        variant.save(update_fields=[spec.field, "updated_at"])
        record_variant_change(actor=actor, variant=variant, old=old, note=note)
        changed += 1
    return changed
