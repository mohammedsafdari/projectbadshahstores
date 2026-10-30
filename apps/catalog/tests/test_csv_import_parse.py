"""Tests for the CSV import parser (Slice 20b, ADR D47).

These tests need no database: the parser only reads bytes and checks the file.
``make_csv`` builds a file from dictionaries, starting from one valid row, so
each test changes only the cell it is about.
"""

import csv
import hashlib
import io
from decimal import Decimal

import pytest

from apps.catalog import csv_import
from apps.catalog.csv_export import CSV_COLUMNS
from apps.catalog.csv_import import ParseResult, parse_variants_csv

# One valid row. A test copies it and changes some cells.
VALID_ROW: dict[str, str] = {
    "sku_code": "ATTA-5KG",
    "barcode": "8901234567890",
    "product_slug": "chakki-atta",
    "product_name_en": "Chakki Atta",
    "product_name_hi": "चक्की आटा",
    "brand_slug": "aashirvaad",
    "brand_name": "Aashirvaad",
    "category_slug": "atta",
    "category_name": "Atta",
    "is_own_brand": "FALSE",
    "diet_type": "VEG",
    "description": "Whole wheat flour",
    "search_keywords": "atta flour",
    "unit": "KG",
    "pack_size": "5.000",
    "is_sold_loose": "FALSE",
    "quantity_step": "1.000",
    "low_stock_threshold": "10.000",
    "hsn_code": "1101",
    "gst_rate_percent": "5.00",
    "mrp": "280.00",
    "selling_price": "265.00",
    "cost_price": "240.00",
    "net_quantity_label": "5 kg",
    "best_before_label": "6 months",
    "shelf_life": "6 months",
    "ingredients": "Wheat",
    "allergens": "Gluten",
    "manufacturer_details": "ITC Ltd",
    "product_is_active": "TRUE",
    "variant_is_active": "TRUE",
}


def make_csv(
    *rows: dict[str, str],
    delimiter: str = ",",
    columns: tuple[str, ...] = CSV_COLUMNS,
    bom: bool = True,
) -> bytes:
    """Build a file. Each row is VALID_ROW with the given cells replaced."""
    buffer = io.StringIO()
    if bom:
        buffer.write("\ufeff")
    writer = csv.writer(buffer, delimiter=delimiter)
    writer.writerow(columns)
    for changes in rows:
        row = {**VALID_ROW, **changes}
        writer.writerow([row.get(name, "") for name in columns])
    return buffer.getvalue().encode("utf-8")


def parse_one(**changes: str) -> ParseResult:
    return parse_variants_csv(make_csv(changes))


def messages(result: ParseResult, column: str) -> list[str]:
    return [issue.message for issue in result.issues if issue.column == column]


# ---------------------------------------------------------------------------
# A good file
# ---------------------------------------------------------------------------
def test_valid_file_gives_typed_values() -> None:
    result = parse_one()
    assert result.ok
    assert result.row_count == 1
    row = result.rows[0]
    assert row.line == 2  # the header is line 1
    values = row.values
    assert values["sku_code"] == "ATTA-5KG"
    assert values["barcode"] == "8901234567890"
    assert values["product_name_hi"] == "चक्की आटा"
    assert values["is_own_brand"] is False
    assert values["product_is_active"] is True
    assert values["unit"] == "KG"
    assert values["mrp"] == Decimal("280.00")
    assert values["pack_size"] == Decimal("5.000")
    assert values["cost_price"] == Decimal("240.00")
    # The two name columns are for people. The import ignores them.
    assert "brand_name" not in values
    assert "category_name" not in values


def test_sha256_is_the_hash_of_the_bytes() -> None:
    raw = make_csv({})
    assert parse_variants_csv(raw).sha256 == hashlib.sha256(raw).hexdigest()


def test_file_without_bom_and_with_columns_in_another_order() -> None:
    reordered = tuple(reversed(CSV_COLUMNS))
    result = parse_variants_csv(make_csv({}, columns=reordered, bom=False))
    assert result.ok
    assert result.rows[0].values["sku_code"] == "ATTA-5KG"


@pytest.mark.parametrize("delimiter", [";", "\t"])
def test_semicolon_and_tab_files_are_read(delimiter: str) -> None:
    result = parse_variants_csv(make_csv({}, delimiter=delimiter))
    assert result.ok
    assert result.delimiter == delimiter
    assert result.rows[0].values["mrp"] == Decimal("280.00")


def test_blank_lines_are_skipped_and_line_numbers_stay_right() -> None:
    raw = make_csv({"sku_code": "A-1"}, {"sku_code": "A-2", "barcode": "", "mrp": "bad"})
    lines = raw.decode("utf-8").splitlines()
    lines.insert(2, "")  # a blank line between the two data rows
    result = parse_variants_csv("\n".join(lines).encode("utf-8"))
    assert result.row_count == 2
    assert [issue.line for issue in result.issues] == [4]  # header 1, row 2, blank 3, row 4


def test_line_number_after_a_cell_that_spans_two_lines() -> None:
    first = {"sku_code": "A-1", "description": "line one\nline two"}
    result = parse_variants_csv(make_csv(first, {"sku_code": "A-2", "mrp": "bad"}))
    assert result.rows[0].values["description"] == "line one\nline two"
    assert [issue.line for issue in result.issues] == [4]  # the first row uses lines 2 and 3


# ---------------------------------------------------------------------------
# Problems with the whole file
# ---------------------------------------------------------------------------
def test_empty_file() -> None:
    result = parse_variants_csv(b"  \n ")
    assert not result.ok
    assert result.issues[0].line == 0
    assert "empty" in result.issues[0].message


def test_file_too_large() -> None:
    result = parse_variants_csv(b"x" * (csv_import.MAX_FILE_BYTES + 1))
    assert "larger than 2 MB" in result.issues[0].message


def test_file_not_utf8() -> None:
    # The old Excel "CSV (Comma delimited)" format uses cp1252, where é is one byte.
    raw = make_csv({"product_name_en": "Café", "product_name_hi": ""}, bom=False)
    result = parse_variants_csv(raw.decode("utf-8").encode("cp1252"))
    assert "not UTF-8" in result.issues[0].message


def test_nul_character_in_file() -> None:
    result = parse_variants_csv(make_csv({}) + b"\x00")
    assert "NUL" in result.issues[0].message


def test_no_header_line() -> None:
    result = parse_variants_csv(b"name,price\nTea,50\n")
    assert "No header line" in result.issues[0].message


def test_header_only_file_has_no_rows() -> None:
    result = parse_variants_csv(make_csv())
    assert "no data rows" in result.issues[0].message


def test_missing_unknown_and_repeated_columns() -> None:
    columns = (*(c for c in CSV_COLUMNS if c != "mrp"), "colour", "sku_code")
    result = parse_variants_csv(make_csv({}, columns=columns))
    text = " ".join(issue.message for issue in result.issues)
    assert "Missing columns: mrp" in text
    assert "'colour'" in text
    assert "Repeated columns: sku_code" in text
    assert result.rows == []  # no row is read when the header is wrong


def test_upper_case_header_names_are_refused() -> None:
    columns = tuple("MRP" if c == "mrp" else c for c in CSV_COLUMNS)
    result = parse_variants_csv(make_csv({}, columns=columns))
    text = " ".join(issue.message for issue in result.issues)
    assert "Missing columns: mrp" in text
    assert "'MRP'" in text


def test_too_many_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(csv_import, "MAX_DATA_ROWS", 2)
    rows = [{"sku_code": f"S-{n}", "barcode": ""} for n in range(3)]
    result = parse_variants_csv(make_csv(*rows))
    assert "more than 2 data rows" in result.issues[0].message


def test_unclosed_quote() -> None:
    raw = make_csv({}) + b'S-9,"never closed\n'
    result = parse_variants_csv(raw)
    assert not result.ok
    assert result.issues[-1].line >= 2


def test_row_with_wrong_number_of_cells() -> None:
    raw = make_csv({"sku_code": "A-1"}) + b"S-9,123\n"
    result = parse_variants_csv(raw)
    assert result.issues[0].line == 3
    assert "2 cells but the header has 31" in result.issues[0].message
    assert len(result.rows) == 1  # the good row is still returned


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("column", "value", "fragment"),
    [
        ("mrp", "1,250.50", "comma"),
        ("mrp", "95,50", "comma"),
        ("mrp", "1.25E+3", "scientific"),
        ("mrp", "abc", "not a number"),
        ("mrp", ".5", "not a number"),
        ("mrp", "5.", "not a number"),
        ("mrp", "", "Required"),
        ("mrp", "0", "more than 0"),
        ("mrp", "-5", "more than 0"),
        ("mrp", "280.005", "more than 2 decimal places"),
        ("mrp", "12345678901", "too large"),
        ("mrp", "1" * 31, "Too long"),
        ("mrp", "١٢٣", "not a number"),  # Arabic-Indic digits
        ("mrp", "१२३", "not a number"),  # Devanagari digits
        ("gst_rate_percent", "100.01", "at most 100"),
        ("gst_rate_percent", "-1", "at least 0"),
        ("pack_size", "0", "at least 0.001"),
        ("pack_size", "1.0005", "more than 3 decimal places"),
        ("cost_price", "-1", "at least 0"),
        ("low_stock_threshold", "-0.5", "at least 0"),
    ],
)
def test_bad_numbers(column: str, value: str, fragment: str) -> None:
    result = parse_one(**{column: value})
    assert result.rows == []
    assert any(fragment in message for message in messages(result, column)), result.issues


def test_extra_zeros_are_accepted_and_value_is_rounded_to_the_column() -> None:
    values = parse_one(mrp="280.000", selling_price="265", gst_rate_percent="5").rows[0].values
    assert values["mrp"] == Decimal("280.00")
    assert str(values["mrp"]) == "280.00"
    assert values["selling_price"] == Decimal("265.00")
    assert values["gst_rate_percent"] == Decimal("5.00")


def test_optional_numbers_may_be_blank() -> None:
    values = parse_one(cost_price="", low_stock_threshold="").rows[0].values
    assert values["cost_price"] is None
    assert values["low_stock_threshold"] is None


def test_selling_price_above_mrp() -> None:
    result = parse_one(mrp="100", selling_price="100.01")
    assert "cannot be higher than the MRP" in messages(result, "selling_price")[0]


def test_selling_price_equal_to_mrp_is_fine() -> None:
    assert parse_one(mrp="100", selling_price="100").ok


def test_a_bad_mrp_is_not_reported_a_second_time_as_a_price_clash() -> None:
    result = parse_one(mrp="abc", selling_price="100")
    assert messages(result, "selling_price") == []


# ---------------------------------------------------------------------------
# Barcode and HSN
# ---------------------------------------------------------------------------
def test_blank_barcode_is_none() -> None:
    assert parse_one(barcode="").rows[0].values["barcode"] is None


def test_barcode_in_scientific_notation() -> None:
    result = parse_one(barcode="8.9E+12")
    assert "scientific notation" in messages(result, "barcode")[0]


@pytest.mark.parametrize("value", ["89012A4567890", "8901 234", "-12345678", "१२३४५६७८"])
def test_barcode_must_be_digits_only(value: str) -> None:
    assert "digits only" in messages(parse_one(barcode=value), "barcode")[0]


def test_barcode_too_long() -> None:
    assert "Too long" in messages(parse_one(barcode="1" * 33), "barcode")[0]


@pytest.mark.parametrize("value", ["1101", "110110", "09021010", "0902"])
def test_good_hsn_codes(value: str) -> None:
    assert parse_one(hsn_code=value).rows[0].values["hsn_code"] == value


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        ("902", "4 to 8 digits"),
        ("123456789", "4 to 8 digits"),
        ("9021010", "leading zero"),  # 09021010 after Excel
        ("11011", "leading zero"),
        ("11A1", "digits only"),
        ("११०१", "digits only"),
        ("", "Required"),
    ],
)
def test_bad_hsn_codes(value: str, fragment: str) -> None:
    result = parse_one(hsn_code=value)
    assert fragment in messages(result, "hsn_code")[0]


# ---------------------------------------------------------------------------
# Flags, choices, slugs, text
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("cell", "expected"), [("TRUE", True), ("false", False), (" True ", True)])
def test_flags_in_any_case(cell: str, expected: bool) -> None:
    assert parse_one(is_own_brand=cell).rows[0].values["is_own_brand"] is expected


@pytest.mark.parametrize("cell", ["1", "yes", "", "Y"])
def test_flags_must_be_true_or_false(cell: str) -> None:
    assert "TRUE or FALSE" in messages(parse_one(variant_is_active=cell), "variant_is_active")[0]


def test_unit_and_diet_type_in_any_case_are_normalised() -> None:
    values = parse_one(unit="kg", diet_type="non_veg").rows[0].values
    assert values["unit"] == "KG"
    assert values["diet_type"] == "NON_VEG"


def test_unknown_unit_and_diet_type() -> None:
    result = parse_one(unit="LITRE", diet_type="VEGAN")
    assert "is not allowed" in messages(result, "unit")[0]
    assert "is not allowed" in messages(result, "diet_type")[0]


@pytest.mark.parametrize("slug", ["Atta Flour", "आटा", "atta/flour", "atta.flour", "atta\u00e9"])
def test_bad_slugs(slug: str) -> None:
    assert "not a valid slug" in messages(parse_one(product_slug=slug), "product_slug")[0]


def test_slug_with_underscore_and_capitals_is_accepted() -> None:
    assert parse_one(brand_slug="My_Brand-2").rows[0].values["brand_slug"] == "My_Brand-2"


def test_required_text_and_length_limits() -> None:
    result = parse_one(sku_code="", product_name_en="x" * 201, net_quantity_label="y" * 61)
    assert "Required" in messages(result, "sku_code")[0]
    assert "Too long" in messages(result, "product_name_en")[0]
    assert "Too long" in messages(result, "net_quantity_label")[0]


def test_sku_of_exactly_40_characters_is_fine() -> None:
    assert parse_one(sku_code="S" * 40).ok


def test_text_is_stripped_and_optional_text_may_be_blank() -> None:
    values = parse_one(product_name_en="  Chakki Atta  ", ingredients="").rows[0].values
    assert values["product_name_en"] == "Chakki Atta"
    assert values["ingredients"] == ""


def test_control_characters() -> None:
    result = parse_one(
        product_name_en="Atta\x07", description="ok\nfine\ttoo", ingredients="a\x1bb"
    )
    assert "control character" in messages(result, "product_name_en")[0]
    assert "control character" in messages(result, "ingredients")[0]
    assert messages(result, "description") == []  # a newline and a tab are fine here


def test_a_newline_in_a_one_line_column_is_refused() -> None:
    assert "control character" in messages(parse_one(shelf_life="1\n2"), "shelf_life")[0]


def test_brand_name_and_category_name_are_ignored_even_when_odd() -> None:
    assert parse_one(brand_name="\x07 whatever", category_name="").ok


# ---------------------------------------------------------------------------
# The formula guard from the export
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("starter", ["=", "+", "-", "@"])
def test_guard_quote_is_removed(starter: str) -> None:
    name = f"{starter}SUM(A1)"
    values = parse_one(product_name_en="'" + name).rows[0].values
    assert values["product_name_en"] == name


def test_name_that_really_starts_with_a_quote_is_kept() -> None:
    assert parse_one(product_name_en="'Tis Tea").rows[0].values["product_name_en"] == "'Tis Tea"


def test_only_one_quote_is_removed() -> None:
    assert parse_one(product_name_en="''=x").rows[0].values["product_name_en"] == "''=x"


def test_guard_is_not_applied_to_number_columns() -> None:
    assert "not a number" in messages(parse_one(mrp="'-5"), "mrp")[0]


# ---------------------------------------------------------------------------
# Rules that compare cells
# ---------------------------------------------------------------------------
def test_loose_item_needs_kg_or_l_and_pack_size_one() -> None:
    bad = parse_one(is_sold_loose="TRUE", unit="G", pack_size="5", quantity_step="0.1")
    assert "KG or L" in messages(bad, "unit")[0]
    assert "pack size 1" in messages(bad, "pack_size")[0]
    good = parse_one(is_sold_loose="TRUE", unit="KG", pack_size="1", quantity_step="0.100")
    assert good.ok


def test_packed_item_needs_quantity_step_one() -> None:
    result = parse_one(is_sold_loose="FALSE", quantity_step="0.5")
    assert "quantity step 1" in messages(result, "quantity_step")[0]


# ---------------------------------------------------------------------------
# Rules across rows
# ---------------------------------------------------------------------------
def test_duplicate_sku_in_file() -> None:
    result = parse_variants_csv(
        make_csv({"sku_code": "A-1", "barcode": ""}, {"sku_code": "A-1", "barcode": ""})
    )
    assert [i.line for i in result.issues] == [3]
    assert "already appears on line 2" in result.issues[0].message
    assert [r.line for r in result.rows] == [2]  # the first row is kept


def test_sku_differing_only_in_case() -> None:
    result = parse_variants_csv(
        make_csv({"sku_code": "Atta-1", "barcode": ""}, {"sku_code": "ATTA-1", "barcode": ""})
    )
    assert "differs only in letter case" in result.issues[0].message


def test_duplicate_barcode_in_file() -> None:
    result = parse_variants_csv(make_csv({"sku_code": "A-1"}, {"sku_code": "A-2"}))
    assert messages(result, "barcode") == ["Barcode '8901234567890' already appears on line 2."]


def test_blank_barcodes_do_not_clash() -> None:
    result = parse_variants_csv(
        make_csv({"sku_code": "A-1", "barcode": ""}, {"sku_code": "A-2", "barcode": ""})
    )
    assert result.ok


def test_two_variants_of_one_product_may_share_product_columns() -> None:
    result = parse_variants_csv(
        make_csv(
            {"sku_code": "A-1", "barcode": "", "pack_size": "1"},
            {"sku_code": "A-5", "barcode": "", "pack_size": "5"},
        )
    )
    assert result.ok
    assert len(result.rows) == 2


def test_rows_of_one_product_must_agree() -> None:
    result = parse_variants_csv(
        make_csv(
            {"sku_code": "A-1", "barcode": ""},
            {
                "sku_code": "A-5",
                "barcode": "",
                "product_name_en": "Other name",
                "diet_type": "NON_VEG",
            },
        )
    )
    assert {i.column for i in result.issues} == {"product_name_en", "diet_type"}
    assert all("Differs from line 2" in i.message for i in result.issues)


def test_bad_rows_are_dropped_and_good_rows_stay() -> None:
    result = parse_variants_csv(
        make_csv(
            {"sku_code": "A-1", "barcode": ""},
            {"sku_code": "A-2", "barcode": "", "mrp": "bad"},
            {"sku_code": "A-3", "barcode": ""},
        )
    )
    assert result.row_count == 3
    assert [r.values["sku_code"] for r in result.rows] == ["A-1", "A-3"]
    assert not result.ok


def test_one_row_shows_all_its_problems_at_once() -> None:
    result = parse_one(mrp="abc", hsn_code="9021010", unit="LITRE", variant_is_active="maybe")
    assert {i.column for i in result.issues} == {"mrp", "hsn_code", "unit", "variant_is_active"}
