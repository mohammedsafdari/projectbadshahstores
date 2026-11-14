"""The staff search-word report: the page, the CSV and the query count (D89)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import User
from apps.orders.tests.staff_helpers import staff
from apps.reports.queries import DateRange, search_words_report
from apps.reports.tests.helpers import FIRST_DAY, LAST_DAY, member_client, staff_client
from apps.storefront.models import SearchTermDay

pytestmark = pytest.mark.django_db

NOVEMBER = DateRange(FIRST_DAY, LAST_DAY)

# The range every page and CSV call asks for, so a test never depends on today.
RANGE = {"from": FIRST_DAY.isoformat(), "to": LAST_DAY.isoformat()}

PAGE_URL = "reports_search_words"
CSV_URL = "reports_search_words_csv"


def _term(term: str, searches: int, empty: int = 0, *, day: date = FIRST_DAY) -> SearchTermDay:
    """One day's counter for one word."""
    return SearchTermDay.objects.create(
        day=day,
        term=term,
        searches=searches,
        empty_searches=empty,
    )


def _section(html: str, name: str) -> str:
    """One table of the page, so a word in the other table cannot match."""
    marker = f'data-search-words="{name}"'
    assert marker in html
    return html.split(marker, 1)[1].split("</section>", 1)[0]


def _page(client: Client, **params: str) -> str:
    values = dict(RANGE)
    values.update(params)
    return client.get(reverse(PAGE_URL), values).content.decode()


def _csv_rows(client: Client, **params: str) -> list[list[str]]:
    values = dict(RANGE)
    values.update(params)
    response = client.get(reverse(CSV_URL), values)
    assert response.status_code == 200
    body = response.content.decode("utf-8-sig")
    # No cell of this file holds a comma, so a plain split reads the rows.
    return [line.split(",") for line in body.splitlines() if line]


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------
def test_page_needs_view_reports() -> None:
    url = reverse(PAGE_URL)

    anonymous = Client().get(url)
    assert anonymous.status_code == 302
    assert reverse("account_login") in anonymous["Location"]
    assert member_client().get(url).status_code == 403
    assert staff_client(User.ROLE_PACKER).get(url).status_code == 403
    assert staff_client(User.ROLE_OWNER).get(url).status_code == 200


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------
def test_page_lists_most_searched_in_range() -> None:
    _term("atta", 9)
    _term("rice", 4)

    section = _section(_page(staff_client()), "most")

    assert "atta" in section
    assert "rice" in section
    # The most searched word comes first.
    assert section.index("atta") < section.index("rice")


def test_page_lists_no_result_terms_only_with_empty_searches() -> None:
    _term("found-something", 5)
    _term("nothing-here", 3, empty=3)

    section = _section(_page(staff_client()), "empty")

    assert "nothing-here" in section
    # A word that always found something has nothing to say in this list.
    assert "found-something" not in section


def test_range_filter() -> None:
    _term("inside", 5, day=FIRST_DAY)
    _term("outside", 5, day=LAST_DAY + timedelta(days=29))

    response = staff_client().get(
        reverse(PAGE_URL),
        {"from": FIRST_DAY.isoformat(), "to": LAST_DAY.isoformat()},
    )

    html = response.content.decode()
    assert "inside" in html
    assert "outside" not in html


def test_bad_range_shows_message() -> None:
    response = staff_client().get(reverse(PAGE_URL), {"from": "not-a-date"})

    assert response.status_code == 200
    assert "Enter both dates as YYYY-MM-DD" in response.content.decode()


# ---------------------------------------------------------------------------
# The CSV
# ---------------------------------------------------------------------------
def test_csv_has_both_lists() -> None:
    _term("atta", 9)
    _term("nothing-here", 3, empty=3)

    rows = _csv_rows(staff_client())

    assert rows[0] == ["list", "term", "searches", "empty_searches"]
    assert ["Most searched", "atta", "9", "0"] in rows
    assert ["No results", "nothing-here", "3", "3"] in rows


def test_csv_matches_page() -> None:
    _term("atta", 9)
    _term("rice", 4)
    client = staff_client()

    rows = [row for row in _csv_rows(client) if row[0] == "Most searched"]
    section = _section(_page(client), "most")

    assert [row[1] for row in rows] == ["atta", "rice"]
    for row in rows:
        assert row[1] in section


def test_index_links_to_search_words() -> None:
    html = staff_client().get(reverse("reports_index")).content.decode()

    assert reverse(PAGE_URL) in html


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def _count(call: Callable[[], object]) -> int:
    with CaptureQueriesContext(connection) as queries:
        call()
    return len(queries)


def test_query_count_does_not_grow_with_terms() -> None:
    actor = staff()
    _term("atta", 9)
    _term("rice", 4)

    small = _count(lambda: search_words_report(actor, NOVEMBER))
    for index in range(6):
        _term(f"word {index}", index + 1, empty=index)
    large = _count(lambda: search_words_report(actor, NOVEMBER))

    assert len(search_words_report(actor, NOVEMBER).most_searched) == 2 + 6
    assert small == large
