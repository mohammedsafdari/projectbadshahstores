"""CSV import of catalog variants: parse and check the file (Slice 20b, ADR D47).

The import reads the same columns the export writes (``CSV_COLUMNS``), so a file
exported by Slice 19 can be edited and imported back.

This module so far does the first of three steps and touches no database:

1. **parse** (this file): bytes in, cleaned and typed rows out, plus a list of
   problems. Every rule that needs only the file itself lives here.
2. plan (Slice 20c): compare the rows with the database and decide CREATE,
   UPDATE, UNCHANGED or ERROR for each row. Writes nothing.
3. apply (Slice 20c): write everything inside one transaction.

REVIEW CAREFULLY: the file comes from a person and from Excel, so nothing in it
is trusted. A harmless-looking file goes wrong in known ways (semicolons,
decimal commas, barcodes in scientific notation, lost leading zeros). Each one
gets a clear error here instead of a guess.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from apps.catalog.csv_export import CSV_COLUMNS, TEXT_COLUMNS
from apps.catalog.models import Product, ProductVariant

# ---------------------------------------------------------------------------
# Limits (plain constants so tests and docs can import them)
# ---------------------------------------------------------------------------
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_DATA_ROWS = 5000

# The delimiters we accept. Excel on a German-locale PC saves semicolons, on an
# Indian-locale PC commas. The delimiter is read from the header line.
_DELIMITERS = (",", ";", "\t")

# Columns that are read for humans only. The import ignores them (ADR D47 point 3).
IGNORED_COLUMNS: frozenset[str] = frozenset({"brand_name", "category_name"})

# Columns that describe the product, not the variant. Rows with the same
# product_slug must agree on all of them.
PRODUCT_COLUMNS: tuple[str, ...] = (
    "product_name_en",
    "product_name_hi",
    "brand_slug",
    "category_slug",
    "is_own_brand",
    "diet_type",
    "description",
    "search_keywords",
    "product_is_active",
)

# Text columns that may hold several lines. Every other text cell must be one line.
_MULTILINE_COLUMNS: frozenset[str] = frozenset(
    {"description", "search_keywords", "ingredients", "allergens", "manufacturer_details"}
)

# Longest allowed text per column, copied from the model fields.
_MAX_LENGTHS: dict[str, int] = {
    "sku_code": 40,
    "barcode": 32,
    "product_slug": 220,
    "product_name_en": 200,
    "product_name_hi": 200,
    "brand_slug": 140,
    "category_slug": 140,
    "net_quantity_label": 60,
    "best_before_label": 120,
    "shelf_life": 60,
}

# Text columns that may be left empty. All other text columns must have a value.
_OPTIONAL_TEXT: frozenset[str] = frozenset(
    {
        "product_name_hi",
        "description",
        "search_keywords",
        "net_quantity_label",
        "best_before_label",
        "shelf_life",
        "ingredients",
        "allergens",
        "manufacturer_details",
    }
)

_SLUG_COLUMNS = ("product_slug", "brand_slug", "category_slug")
_FLAG_COLUMNS = ("is_own_brand", "is_sold_loose", "product_is_active", "variant_is_active")


@dataclass(frozen=True)
class NumberRule:
    """Limits of one decimal column, copied from the model field."""

    max_digits: int
    decimal_places: int
    minimum: Decimal
    minimum_allowed: bool  # True: value >= minimum is fine. False: value must be > minimum.
    maximum: Decimal | None = None
    optional: bool = False


_ZERO = Decimal("0")
_THOUSANDTH = Decimal("0.001")

# ``minimum_allowed=False`` with minimum 0 means "greater than zero".
_NUMBER_RULES: dict[str, NumberRule] = {
    "pack_size": NumberRule(10, 3, _THOUSANDTH, True),
    "quantity_step": NumberRule(8, 3, _THOUSANDTH, True),
    "low_stock_threshold": NumberRule(12, 3, _ZERO, True, optional=True),
    "gst_rate_percent": NumberRule(5, 2, _ZERO, True, maximum=Decimal("100")),
    "mrp": NumberRule(10, 2, _ZERO, False),
    "selling_price": NumberRule(10, 2, _ZERO, False),
    "cost_price": NumberRule(10, 2, _ZERO, True, optional=True),
}

# re.ASCII matters: without it, \d also matches digits of other scripts (for example
# the Devanagari digits of a Hindi keyboard), which Decimal() and the database
# would accept but nobody can read on a barcode. fullmatch() must match the whole
# text, so there is no "$" that would let a trailing newline slip through.
_SLUG_RE = re.compile(r"[-a-zA-Z0-9_]+", re.ASCII)
_PLAIN_NUMBER_RE = re.compile(r"-?\d+(\.\d+)?", re.ASCII)
_SCIENTIFIC_RE = re.compile(r"[-+]?\d+(\.\d+)?[eE][-+]?\d+", re.ASCII)
_DIGITS_RE = re.compile(r"\d+", re.ASCII)
# A cell longer than this cannot be a valid number for any column.
_MAX_NUMBER_TEXT = 30

# Excel and Google Sheets treat a cell that starts with one of these as a formula.
# The export puts a single quote in front of such a cell (D46 point 5); we remove it.
_FORMULA_STARTERS = ("=", "+", "-", "@", "\t", "\r")


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Issue:
    """One problem found in the file.

    ``line`` is the line in the file where the row starts (the header is line 1).
    It is 0 for a problem with the whole file. ``column`` is empty when the
    problem is not about one column.
    """

    line: int
    column: str
    message: str


@dataclass(frozen=True)
class ParsedRow:
    """One data row after cleaning: ``values`` holds typed values, by column name.

    Text is ``str`` (empty string when blank), numbers are ``Decimal``, flags are
    ``bool``. ``barcode``, ``low_stock_threshold`` and ``cost_price`` are ``None``
    when the cell is blank. ``brand_name`` and ``category_name`` are not included.
    """

    line: int
    values: dict[str, Any]


@dataclass
class ParseResult:
    """What ``parse_variants_csv`` returns.

    ``rows`` holds only the rows that passed every check, so the next step can
    still check them against the database and report all problems in one go.
    ``ok`` is True only when the whole file is clean.
    """

    sha256: str = ""
    delimiter: str = ""
    rows: list[ParsedRow] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    row_count: int = 0  # data rows seen, including the ones with problems

    @property
    def ok(self) -> bool:
        return not self.issues


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _shown(value: str, limit: int = 40) -> str:
    """Quote a cell value for a message, cut short so a huge cell cannot flood the report."""
    text = value if len(value) <= limit else value[: limit - 1] + "…"
    return f"'{text}'"


def _unguard(value: str) -> str:
    """Undo the export's formula guard (D46 point 5).

    The export turns ``=SUM(A1)`` into ``'=SUM(A1)``. Here one leading quote is
    removed again, but only when the character after it is a formula starter, so
    a name that really starts with a quote (``'Tis``) is left alone.
    """
    if len(value) > 1 and value[0] == "'" and value[1] in _FORMULA_STARTERS:
        return value[1:]
    return value


def _has_bad_control_character(value: str, multiline: bool) -> bool:
    """True when the text holds a control character (NUL, bell, escape, ...).

    ``unicodedata.category`` returns a two-letter class; "Cc" means control
    character. Several-line text columns may hold a newline or a tab.
    """
    allowed = "\n\r\t" if multiline else ""
    return any(unicodedata.category(ch) == "Cc" and ch not in allowed for ch in value)


def _is_blank_row(cells: list[str]) -> bool:
    return all(not cell.strip() for cell in cells)


# ---------------------------------------------------------------------------
# Cell checks. Each returns the cleaned value, or None after adding an Issue.
# ---------------------------------------------------------------------------
class _RowChecker:
    """Checks the cells of one row and collects the problems in ``issues``."""

    def __init__(self, line: int, issues: list[Issue]) -> None:
        self.line = line
        self.issues = issues

    def error(self, column: str, message: str) -> None:
        self.issues.append(Issue(self.line, column, message))

    # -- text ---------------------------------------------------------------
    def text(self, column: str, raw: str) -> str | None:
        value = raw.strip()
        if column in TEXT_COLUMNS:
            value = _unguard(value)
        if _has_bad_control_character(value, column in _MULTILINE_COLUMNS):
            self.error(column, "Contains a control character. Remove it and try again.")
            return None
        if not value:
            if column in _OPTIONAL_TEXT:
                return ""
            self.error(column, "Required, but empty.")
            return None
        limit = _MAX_LENGTHS.get(column)
        if limit is not None and len(value) > limit:
            self.error(column, f"Too long: {len(value)} characters, the limit is {limit}.")
            return None
        return value

    def slug(self, column: str, raw: str) -> str | None:
        value = self.text(column, raw)
        if value is None:
            return None
        if not _SLUG_RE.fullmatch(value):
            self.error(
                column,
                f"{_shown(value)} is not a valid slug. Use only English letters, "
                "digits, hyphens and underscores.",
            )
            return None
        return value

    def choice(self, column: str, raw: str, choices: set[str]) -> str | None:
        """A fixed list of values (unit, diet type). Upper and lower case both work."""
        value = self.text(column, raw)
        if value is None:
            return None
        value = value.upper()
        if value not in choices:
            self.error(
                column, f"{_shown(value)} is not allowed. Use one of: {', '.join(sorted(choices))}."
            )
            return None
        return value

    # -- flags --------------------------------------------------------------
    def flag(self, column: str, raw: str) -> bool | None:
        value = raw.strip().upper()
        if value == "TRUE":
            return True
        if value == "FALSE":
            return False
        self.error(column, f"Must be TRUE or FALSE, not {_shown(raw.strip())}.")
        return None

    # -- barcode and HSN ----------------------------------------------------
    def barcode(self, raw: str) -> tuple[bool, str | None]:
        """Return (ok, value). A blank barcode is fine and gives (True, None)."""
        value = _unguard(raw.strip())
        if not value:
            return True, None
        if _SCIENTIFIC_RE.fullmatch(value):
            self.error(
                "barcode",
                f"{_shown(value)} looks like Excel turned a long number into scientific "
                "notation. Format the column as Text before typing barcodes, or import the "
                "file without opening it in Excel.",
            )
            return False, None
        if not _DIGITS_RE.fullmatch(value):
            self.error("barcode", f"{_shown(value)} must contain digits only.")
            return False, None
        if len(value) > _MAX_LENGTHS["barcode"]:
            self.error(
                "barcode",
                f"Too long: {len(value)} digits, the limit is {_MAX_LENGTHS['barcode']}.",
            )
            return False, None
        return True, value

    def hsn(self, raw: str) -> str | None:
        value = self.text("hsn_code", raw)
        if value is None:
            return None
        if not _DIGITS_RE.fullmatch(value):
            self.error("hsn_code", f"{_shown(value)} must contain digits only.")
            return None
        if not 4 <= len(value) <= 8:
            self.error(
                "hsn_code",
                f"{_shown(value)} has {len(value)} digits. An HSN code has 4 to 8 digits. "
                "If Excel removed a leading zero, type it again as text.",
            )
            return None
        # HSN codes have 2, 4, 6 or 8 digits. An odd length is almost always a
        # code whose leading zero was dropped, for example 09021010 -> 9021010.
        if len(value) % 2 == 1:
            self.error(
                "hsn_code",
                f"{_shown(value)} has {len(value)} digits, but HSN codes have 4, 6 or 8. "
                "Excel often removes a leading zero (09021010 becomes 9021010). "
                "Check the code and type it again as text.",
            )
            return None
        return value

    # -- decimal numbers ----------------------------------------------------
    def number(self, column: str, raw: str) -> tuple[bool, Decimal | None]:
        """Return (ok, value). A blank optional number gives (True, None)."""
        rule = _NUMBER_RULES[column]
        text = raw.strip()
        if not text:
            if rule.optional:
                return True, None
            self.error(column, "Required, but empty.")
            return False, None
        if len(text) > _MAX_NUMBER_TEXT:
            self.error(column, "Too long to be a number for this column.")
            return False, None
        if "," in text:
            self.error(
                column,
                f"{_shown(text)} contains a comma. Use a dot as the decimal point and "
                "no thousands separator, for example 1250.50.",
            )
            return False, None
        if _SCIENTIFIC_RE.fullmatch(text):
            self.error(column, f"{_shown(text)} is in scientific notation. Write the plain number.")
            return False, None
        if not _PLAIN_NUMBER_RE.fullmatch(text):
            self.error(
                column, f"{_shown(text)} is not a number. Use digits and a dot, for example 95.50."
            )
            return False, None

        try:
            value = Decimal(text)
        except InvalidOperation:  # cannot happen after the pattern check; kept as a guard
            self.error(column, f"{_shown(text)} is not a number.")
            return False, None

        # "95.000" is fine for a 2-place column: only non-zero digits count.
        places = Decimal(1).scaleb(-rule.decimal_places)  # 0.01 for 2 places
        rounded = value.quantize(places)
        if rounded != value:
            self.error(
                column, f"{_shown(text)} has more than {rule.decimal_places} decimal places."
            )
            return False, None
        if len(rounded.as_tuple().digits) > rule.max_digits:
            self.error(column, f"{_shown(text)} is too large for this column.")
            return False, None

        below = rounded < rule.minimum if rule.minimum_allowed else rounded <= rule.minimum
        if below:
            relation = "at least" if rule.minimum_allowed else "more than"
            self.error(column, f"{_shown(text)} must be {relation} {rule.minimum.normalize():f}.")
            return False, None
        if rule.maximum is not None and rounded > rule.maximum:
            self.error(column, f"{_shown(text)} must be at most {rule.maximum:f}.")
            return False, None
        return True, rounded


# ---------------------------------------------------------------------------
# One row
# ---------------------------------------------------------------------------
_UNITS = set(ProductVariant.Unit.values)
_DIET_TYPES = set(Product.DietType.values)


def _check_row(line: int, cells: dict[str, str], issues: list[Issue]) -> ParsedRow | None:
    """Clean and check one row. Return None if anything in it is wrong.

    Every column is checked even after the first mistake, so one pass shows the
    person all the problems of the row.
    """
    checker = _RowChecker(line, issues)
    problems_before = len(issues)
    values: dict[str, Any] = {}

    for column in CSV_COLUMNS:
        if column in IGNORED_COLUMNS:
            continue
        raw = cells[column]
        cleaned: Any
        if column in _SLUG_COLUMNS:
            cleaned = checker.slug(column, raw)
        elif column == "unit":
            cleaned = checker.choice(column, raw, _UNITS)
        elif column == "diet_type":
            cleaned = checker.choice(column, raw, _DIET_TYPES)
        elif column in _FLAG_COLUMNS:
            cleaned = checker.flag(column, raw)
        elif column == "barcode":
            _, cleaned = checker.barcode(raw)
        elif column == "hsn_code":
            cleaned = checker.hsn(raw)
        elif column in _NUMBER_RULES:
            _, cleaned = checker.number(column, raw)
        else:
            cleaned = checker.text(column, raw)
        values[column] = cleaned

    # Rules that compare two cells. They run only when both cells were fine, so
    # one mistake is not reported twice.
    def _present(*columns: str) -> bool:
        return all(values.get(c) is not None for c in columns)

    if _present("mrp", "selling_price") and values["selling_price"] > values["mrp"]:
        checker.error("selling_price", "The selling price cannot be higher than the MRP.")
    if _present("is_sold_loose", "unit", "pack_size") and values["is_sold_loose"]:
        if values["unit"] not in {"KG", "L"}:
            checker.error("unit", "A loose item must use the unit KG or L.")
        if values["pack_size"] != 1:
            checker.error("pack_size", "A loose item must have the pack size 1.")
    if (
        _present("is_sold_loose", "quantity_step")
        and not values["is_sold_loose"]
        and values["quantity_step"] != 1
    ):
        checker.error("quantity_step", "A packed item (not loose) must have the quantity step 1.")

    if len(issues) > problems_before:
        return None
    return ParsedRow(line=line, values=values)


# ---------------------------------------------------------------------------
# Rules across rows (still only about the file itself)
# ---------------------------------------------------------------------------
def _check_across_rows(rows: list[ParsedRow], issues: list[Issue]) -> list[ParsedRow]:
    """Find duplicates and rows of one product that disagree. Return the rows that are fine.

    The first row with a given SKU or barcode is kept; later rows with the same
    value get the error. A row that gets an error here is dropped from the result.
    """
    seen_sku: dict[str, int] = {}  # exact SKU -> line of first row
    seen_sku_lower: dict[str, tuple[str, int]] = {}  # lower-case SKU -> (SKU, line)
    seen_barcode: dict[str, int] = {}
    product_first: dict[str, ParsedRow] = {}
    good: list[ParsedRow] = []

    for row in rows:
        before = len(issues)
        values = row.values
        sku = values["sku_code"]

        if sku in seen_sku:
            issues.append(
                Issue(
                    row.line,
                    "sku_code",
                    f"SKU {_shown(sku)} already appears on line {seen_sku[sku]}.",
                )
            )
        elif sku.lower() in seen_sku_lower:
            other, other_line = seen_sku_lower[sku.lower()]
            issues.append(
                Issue(
                    row.line,
                    "sku_code",
                    f"SKU {_shown(sku)} differs only in letter case from {_shown(other)} "
                    f"on line {other_line}.",
                )
            )
        else:
            seen_sku[sku] = row.line
            seen_sku_lower[sku.lower()] = (sku, row.line)

        barcode = values["barcode"]
        if barcode is not None:
            if barcode in seen_barcode:
                issues.append(
                    Issue(
                        row.line,
                        "barcode",
                        f"Barcode {_shown(barcode)} already appears on line "
                        f"{seen_barcode[barcode]}.",
                    )
                )
            else:
                seen_barcode[barcode] = row.line

        first = product_first.setdefault(values["product_slug"], row)
        if first is not row:
            for column in PRODUCT_COLUMNS:
                if values[column] != first.values[column]:
                    issues.append(
                        Issue(
                            row.line,
                            column,
                            f"Differs from line {first.line}, which has the same product_slug. "
                            "All rows of one product must agree on the product columns.",
                        )
                    )

        if len(issues) == before:
            good.append(row)
    return good


# ---------------------------------------------------------------------------
# The whole file
# ---------------------------------------------------------------------------
def _find_delimiter(text: str) -> str | None:
    """Pick the delimiter from the header line: the one that makes ``sku_code`` a cell."""
    first_line = text.split("\n", 1)[0].rstrip("\r")
    for delimiter in _DELIMITERS:
        cells = [cell.strip() for cell in first_line.split(delimiter)]
        if "sku_code" in cells:
            return delimiter
    return None


def _check_header(header: list[str]) -> list[Issue]:
    """File-level problems with the header: missing, unknown or repeated columns."""
    names = [name.strip() for name in header]
    problems: list[Issue] = []
    expected = set(CSV_COLUMNS)

    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        problems.append(Issue(1, "", f"Repeated columns: {', '.join(repeated)}."))
    missing = [name for name in CSV_COLUMNS if name not in names]
    if missing:
        problems.append(Issue(1, "", f"Missing columns: {', '.join(missing)}."))
    unknown = [name for name in names if name not in expected]
    if unknown:
        shown = ", ".join(_shown(name) for name in unknown)
        problems.append(
            Issue(
                1,
                "",
                f"Unknown columns: {shown}. Column names must match the export exactly "
                "(lower case, with underscores).",
            )
        )
    return problems


def parse_variants_csv(raw: bytes) -> ParseResult:
    """Read an uploaded file and return cleaned rows and every problem found.

    This function never raises for a bad file: all problems come back in
    ``ParseResult.issues``. It does not touch the database.
    """
    result = ParseResult(sha256=hashlib.sha256(raw).hexdigest())

    def fail(message: str) -> ParseResult:
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

    delimiter = _find_delimiter(text)
    if delimiter is None:
        return fail(
            "No header line found. The first line must contain the column names, "
            "starting with the same columns as the export (sku_code, ...)."
        )
    result.delimiter = delimiter

    # newline="" lets the csv module handle line breaks inside quoted cells.
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    try:
        header = next(reader)
    except (StopIteration, csv.Error) as exc:
        return fail(f"The header line cannot be read: {exc}")
    header_problems = _check_header(header)
    if header_problems:
        result.issues.extend(header_problems)
        return result
    names = [name.strip() for name in header]

    parsed: list[ParsedRow] = []
    previous_end = reader.line_num  # the header may span lines in rare cases
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
        if _is_blank_row(cells):
            continue
        result.row_count += 1
        if result.row_count > MAX_DATA_ROWS:
            return fail(
                f"The file has more than {MAX_DATA_ROWS} data rows. Split it into smaller files."
            )
        if len(cells) != len(names):
            result.issues.append(
                Issue(
                    start_line,
                    "",
                    f"The row has {len(cells)} cells but the header has {len(names)}. "
                    "Check for a stray delimiter or an unclosed quote.",
                )
            )
            continue
        # zip pairs each column name with its cell; dict() turns the pairs into a lookup.
        row = _check_row(start_line, dict(zip(names, cells, strict=True)), result.issues)
        if row is not None:
            parsed.append(row)

    if result.row_count == 0 and not result.issues:
        return fail("The file has a header but no data rows.")

    result.rows = _check_across_rows(parsed, result.issues)
    return result
