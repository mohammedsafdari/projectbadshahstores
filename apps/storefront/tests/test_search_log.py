"""The private search word log: the cleaning, the counting and the clean-up (D89)."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.utils import timezone

from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.storefront.models import SearchTermDay
from apps.storefront.search_log import clean_term, record_search
from apps.storefront.tasks import delete_old_search_terms

pytestmark = pytest.mark.django_db


def _visible_product(name_en: str):
    """A product with one active variant, so a search can find it."""
    product = ProductFactory(
        name_en=name_en,
        brand=BrandFactory(is_active=True),
        category=CategoryFactory(is_active=True),
        is_active=True,
    )
    ProductVariantFactory(product=product, is_active=True)
    return product


def _today():
    return timezone.localdate()


# ---------------------------------------------------------------------------
# clean_term
# ---------------------------------------------------------------------------
def test_clean_term_lowercases_and_collapses_spaces() -> None:
    assert clean_term("  Green    TEA ") == "green tea"


def test_clean_term_cuts_to_60() -> None:
    term = clean_term("a" * 100)

    assert term is not None
    assert len(term) == 60


def test_clean_term_rejects_short() -> None:
    assert clean_term("ab") is None
    assert clean_term("   a   ") is None


def test_clean_term_rejects_six_digits() -> None:
    assert clean_term("order 123456") is None


def test_clean_term_accepts_five_digits() -> None:
    # Five digits are an odd word, not a phone number or a code.
    assert clean_term(" 12345 atta ") == "12345 atta"


def test_clean_term_rejects_at_sign() -> None:
    assert clean_term("asha@example.com") is None


# ---------------------------------------------------------------------------
# The search page
# ---------------------------------------------------------------------------
def test_search_page_one_records_a_search(client: Client) -> None:
    _visible_product("Parle Biscuits")

    response = client.get("/search/", {"q": "Biscuit"})

    assert response.status_code == 200
    row = SearchTermDay.objects.get()
    assert row.term == "biscuit"
    assert row.searches == 1
    assert row.empty_searches == 0
    assert row.day == _today()


def test_search_page_two_does_not_record(client: Client) -> None:
    for number in range(30):
        _visible_product(f"Match {number:02d}")

    response = client.get("/search/", {"q": "match", "page": "2"})

    assert response.status_code == 200
    assert SearchTermDay.objects.count() == 0


def test_blank_query_records_nothing(client: Client) -> None:
    _visible_product("Parle Biscuits")

    response = client.get("/search/")

    assert response.status_code == 200
    assert SearchTermDay.objects.count() == 0


def test_empty_result_raises_empty_searches(client: Client) -> None:
    _visible_product("Parle Biscuits")

    client.get("/search/", {"q": "zzzz"})

    row = SearchTermDay.objects.get()
    assert row.searches == 1
    assert row.empty_searches == 1


def test_same_term_same_day_raises_counter(client: Client) -> None:
    _visible_product("Parle Biscuits")

    client.get("/search/", {"q": "biscuit"})
    client.get("/search/", {"q": "biscuit"})

    row = SearchTermDay.objects.get()
    assert row.searches == 2


def test_same_term_next_day_makes_new_row() -> None:
    tomorrow = _today() + timedelta(days=1)
    record_search("atta", 3)

    # A stand-in for the timezone module: the clock is moved one day on.
    next_day = SimpleNamespace(localdate=lambda: tomorrow, now=timezone.now)
    with patch("apps.storefront.search_log.timezone", next_day):
        record_search("atta", 3)

    assert sorted(SearchTermDay.objects.values_list("day", flat=True)) == [
        _today(),
        tomorrow,
    ]


def test_suggest_endpoint_records_nothing(client: Client) -> None:
    _visible_product("Parle Biscuits")

    response = client.get("/search/suggest/", {"q": "biscuit"})

    assert response.status_code == 200
    assert SearchTermDay.objects.count() == 0


def test_record_failure_does_not_break_search(client: Client) -> None:
    _visible_product("Parle Biscuits")

    # A broken log table must never stop the page from showing its products.
    with patch("apps.storefront.search_log.SearchTermDay") as broken:
        broken.objects.get_or_create.side_effect = RuntimeError("database down")
        response = client.get("/search/", {"q": "biscuit"})

    assert response.status_code == 200
    assert "Parle Biscuits" in response.content.decode()


# ---------------------------------------------------------------------------
# The row
# ---------------------------------------------------------------------------
def test_row_has_no_user_or_ip_field() -> None:
    names = {field.name for field in SearchTermDay._meta.get_fields()}

    assert names == {
        "id",
        "day",
        "term",
        "searches",
        "empty_searches",
        "created_at",
        "updated_at",
    }


def test_constraint_empty_not_above_searches() -> None:
    # A caught IntegrityError leaves the transaction unusable, so it is the
    # only statement of this test that touches the database.
    with pytest.raises(IntegrityError), transaction.atomic():
        SearchTermDay.objects.create(
            day=_today(),
            term="atta",
            searches=1,
            empty_searches=2,
        )


# ---------------------------------------------------------------------------
# The clean-up
# ---------------------------------------------------------------------------
def test_cleanup_deletes_older_than_180_days() -> None:
    # The numbers are written out, not read from the module: a test that uses
    # the constant would follow any change to it and catch nothing.
    too_old = _today() - timedelta(days=181)
    old = SearchTermDay.objects.create(day=too_old, term="atta")
    SearchTermDay.objects.create(day=_today(), term="rice")

    deleted = delete_old_search_terms()

    assert deleted == 1
    assert not SearchTermDay.objects.filter(pk=old.pk).exists()
    assert SearchTermDay.objects.count() == 1


def test_cleanup_keeps_180_day_old_row() -> None:
    kept = SearchTermDay.objects.create(day=_today() - timedelta(days=180), term="atta")

    deleted = delete_old_search_terms()

    assert deleted == 0
    assert SearchTermDay.objects.filter(pk=kept.pk).exists()


def test_cleanup_beat_entry(settings) -> None:
    entry = settings.CELERY_BEAT_SCHEDULE["delete-old-search-terms"]

    assert entry["task"] == "apps.storefront.tasks.delete_old_search_terms"
    assert entry["schedule"].hour == {3}
    assert entry["schedule"].minute == {30}
