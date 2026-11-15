"""The one helper that decides which product name a visitor reads (D92)."""

from __future__ import annotations

from apps.catalog.names import product_display_name

NAME_EN = "Milk Powder"
NAME_HI = "दूध पाउडर"


def test_english_gets_the_english_name() -> None:
    assert product_display_name(NAME_EN, NAME_HI, "en") == NAME_EN


def test_hindi_gets_the_hindi_name() -> None:
    assert product_display_name(NAME_EN, NAME_HI, "hi") == NAME_HI


def test_hindi_with_a_blank_hindi_name_falls_back_to_english() -> None:
    assert product_display_name(NAME_EN, "", "hi") == NAME_EN


def test_hindi_with_only_spaces_falls_back_to_english() -> None:
    assert product_display_name(NAME_EN, "   ", "hi") == NAME_EN


def test_en_in_gets_the_english_name() -> None:
    assert product_display_name(NAME_EN, NAME_HI, "en-IN") == NAME_EN


def test_a_hi_in_code_counts_as_hindi() -> None:
    assert product_display_name(NAME_EN, NAME_HI, "hi-IN") == NAME_HI


def test_an_unknown_or_missing_language_gets_the_english_name() -> None:
    assert product_display_name(NAME_EN, NAME_HI, None) == NAME_EN
    assert product_display_name(NAME_EN, NAME_HI, "fr") == NAME_EN
