"""Tests for the meta description cutter (Slice 26 follow-up B)."""

from apps.storefront.views import cut_meta


def test_cut_meta_never_longer_than_limit_with_no_space() -> None:
    text = "a" * 200
    result = cut_meta(text, limit=20)
    assert result == "a" * 19 + "\u2026"
    assert len(result) == 20


def test_cut_meta_cuts_at_a_word_and_adds_ellipsis() -> None:
    text = "The quick brown fox jumps over the lazy dog"
    result = cut_meta(text, limit=20)
    assert result == "The quick brown fox\u2026"
    assert len(result) <= 20
