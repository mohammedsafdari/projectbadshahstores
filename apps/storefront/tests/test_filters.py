"""Tests for listing filters parser (Slice 24a)."""

from __future__ import annotations

from decimal import Decimal

from django.http import QueryDict

from apps.storefront.filters import MAX_PRICE, ListingFilters, parse_filters


def test_empty_query_gives_default_filters() -> None:
    filters = parse_filters(QueryDict(""))
    assert filters == ListingFilters()


def test_default_filters_are_not_active() -> None:
    assert parse_filters(QueryDict("")).is_active is False


def test_brand_slugs_are_collected_in_order() -> None:
    filters = parse_filters(QueryDict("brand=a&brand=b"))
    assert filters.brand_slugs == ("a", "b")


def test_brand_slugs_drop_blanks_and_duplicates() -> None:
    filters = parse_filters(QueryDict("brand=a&brand=&brand=a&brand=b"))
    assert filters.brand_slugs == ("a", "b")


def test_valid_prices_are_parsed_as_decimal() -> None:
    filters = parse_filters(QueryDict("min_price=10&max_price=20"))
    assert filters.min_price == Decimal("10")
    assert filters.max_price == Decimal("20")


def test_non_numeric_price_is_ignored() -> None:
    filters = parse_filters(QueryDict("min_price=abc&max_price=xyz"))
    assert filters.min_price is None
    assert filters.max_price is None


def test_negative_price_is_ignored() -> None:
    filters = parse_filters(QueryDict("min_price=-5&max_price=-1"))
    assert filters.min_price is None
    assert filters.max_price is None


def test_nan_and_infinity_prices_are_ignored() -> None:
    filters = parse_filters(QueryDict("min_price=NaN&max_price=Infinity"))
    assert filters.min_price is None
    assert filters.max_price is None


def test_swapped_min_and_max_are_put_in_order() -> None:
    filters = parse_filters(QueryDict("min_price=20&max_price=10"))
    assert filters.min_price == Decimal("10")
    assert filters.max_price == Decimal("20")


def test_checkbox_values_one_on_true_turn_flags_on() -> None:
    filters = parse_filters(QueryDict("own_brand=on&in_stock=1&veg=true"))
    assert filters.own_brand is True
    assert filters.in_stock is True
    assert filters.veg_only is True


def test_other_checkbox_values_leave_flags_off() -> None:
    filters = parse_filters(QueryDict("own_brand=foo&in_stock=&veg=0"))
    assert filters.own_brand is False
    assert filters.in_stock is False
    assert filters.veg_only is False


def test_any_single_filter_makes_filters_active() -> None:
    assert parse_filters(QueryDict("own_brand=on")).is_active is True


def test_price_with_spaces_is_trimmed() -> None:
    filters = parse_filters(QueryDict("min_price=  10  "))
    assert filters.min_price == Decimal("10")


def test_price_above_maximum_is_ignored() -> None:
    filters = parse_filters(QueryDict(f"min_price={MAX_PRICE + 1}"))
    assert filters.min_price is None
