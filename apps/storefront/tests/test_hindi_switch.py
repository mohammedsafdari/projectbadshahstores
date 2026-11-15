"""The Hindi switch: the header form and the POST view (D92).

While the switch is off the shop offers English only, no form shows and the view
answers 404. With it on, one POST changes the language cookie and returns the
visitor to the page they were reading.
"""

from __future__ import annotations

import re

import pytest
from django.conf import settings
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.core.models import ShopSettings

pytestmark = pytest.mark.django_db

LANGUAGE_COOKIE = settings.LANGUAGE_COOKIE_NAME
BOTH_LANGUAGES = [("en-IN", "English"), ("hi", "हिन्दी")]
FORM_START = '<form method="post" action="/language/"'


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """One settings row and an empty cache, so no test sees a stale cache."""
    cache.clear()
    ShopSettings.load()
    yield
    cache.clear()


@pytest.fixture
def hindi_on(settings):
    """The switch on and both languages offered, as a production run has it."""
    settings.HINDI_ENABLED = True
    settings.LANGUAGES = BOTH_LANGUAGES
    return settings


@pytest.fixture
def hindi_off(settings):
    """The switch off, whatever a developer's own .env says."""
    settings.HINDI_ENABLED = False
    settings.LANGUAGES = [("en-IN", "English")]
    return settings


def _switcher_form(html: str) -> str:
    """The language form as one piece of HTML, so a test reads only its fields."""
    match = re.search(FORM_START + r".*?</form>", html, re.DOTALL)
    assert match is not None, "the language form is not on the page"
    return match.group(0)


# ---------------------------------------------------------------------------
# The header form
# ---------------------------------------------------------------------------
def test_the_switcher_is_hidden_while_hindi_is_off(hindi_off, client: Client) -> None:
    html = client.get(reverse("home")).content.decode()

    assert FORM_START not in html
    assert "हिन्दी" not in html


def test_the_switcher_is_shown_while_hindi_is_on(hindi_on, client: Client) -> None:
    form = _switcher_form(client.get(reverse("home")).content.decode())

    assert 'name="language" value="hi"' in form
    assert 'lang="hi"' in form
    assert "हिन्दी" in form


def test_the_switcher_button_says_english_on_a_hindi_page(hindi_on, client: Client) -> None:
    client.cookies[LANGUAGE_COOKIE] = "hi"

    form = _switcher_form(client.get(reverse("home")).content.decode())

    assert 'name="language" value="en-IN"' in form
    assert "English</button>" in form
    assert 'value="hi"' not in form
    assert "हिन्दी" not in form


def test_the_switcher_form_carries_a_csrf_token_and_the_current_path(
    hindi_on, client: Client
) -> None:
    page = client.get(reverse("product_list"), {"sort": "price_asc"}).content.decode()

    form = _switcher_form(page)

    assert "csrfmiddlewaretoken" in form
    assert 'name="next" value="/products/?sort=price_asc"' in form


# ---------------------------------------------------------------------------
# The view
# ---------------------------------------------------------------------------
def test_switch_language_answers_404_while_hindi_is_off(hindi_off, client: Client) -> None:
    response = client.post(reverse("switch_language"), {"language": "hi", "next": "/"})

    assert response.status_code == 404


def test_switch_language_needs_post(hindi_on, client: Client) -> None:
    assert client.get(reverse("switch_language")).status_code == 405


def test_switch_language_sets_the_cookie_to_hindi(hindi_on, client: Client) -> None:
    response = client.post(reverse("switch_language"), {"language": "hi", "next": "/"})

    assert response.status_code == 302
    assert response.cookies[LANGUAGE_COOKIE].value == "hi"


def test_switch_language_can_go_back_to_english(hindi_on, client: Client) -> None:
    response = client.post(reverse("switch_language"), {"language": "en-IN", "next": "/"})

    cookie = response.cookies.get(LANGUAGE_COOKIE)
    assert cookie is not None
    # Django stores a code only when it finds a message catalogue for it, and
    # English has none of its own, so the shop's "en-IN" arrives as "en" (D92).
    assert cookie.value == "en"
    client.cookies[LANGUAGE_COOKIE] = cookie.value
    assert '<html lang="en-IN">' in client.get(reverse("home")).content.decode()


def test_switch_language_ignores_an_unknown_language(hindi_on, client: Client) -> None:
    response = client.post(reverse("switch_language"), {"language": "fr", "next": "/"})

    assert response.status_code == 302
    assert LANGUAGE_COOKIE not in response.cookies


def test_switch_language_redirects_to_the_next_path(hindi_on, client: Client) -> None:
    response = client.post(
        reverse("switch_language"),
        {"language": "hi", "next": "/products/?sort=price_asc"},
    )

    assert response["Location"] == "/products/?sort=price_asc"


def test_switch_language_never_redirects_to_another_host(hindi_on, client: Client) -> None:
    response = client.post(
        reverse("switch_language"),
        {"language": "hi", "next": "https://evil.example/steal"},
    )

    assert response["Location"] == "/"


def test_switch_language_without_next_goes_home(hindi_on, client: Client) -> None:
    response = client.post(reverse("switch_language"), {"language": "hi"})

    assert response["Location"] == "/"


# ---------------------------------------------------------------------------
# The page language
# ---------------------------------------------------------------------------
def test_html_lang_is_en_in_by_default(hindi_off, client: Client) -> None:
    assert '<html lang="en-IN">' in client.get(reverse("home")).content.decode()


def test_html_lang_is_hi_after_choosing_hindi(hindi_on, client: Client) -> None:
    response = client.post(reverse("switch_language"), {"language": "hi", "next": "/"})
    client.cookies[LANGUAGE_COOKIE] = response.cookies[LANGUAGE_COOKIE].value

    page = client.get(reverse("home")).content.decode()

    assert '<html lang="hi">' in page
    assert '<html lang="en-IN">' not in page


def test_a_hindi_cookie_is_ignored_while_hindi_is_off(hindi_off, client: Client) -> None:
    client.cookies[LANGUAGE_COOKIE] = "hi"

    page = client.get(reverse("home")).content.decode()

    assert '<html lang="en-IN">' in page
    assert "हिन्दी" not in page


def test_an_accept_language_header_for_hindi_is_ignored_while_hindi_is_off(
    hindi_off, client: Client
) -> None:
    response = client.get(reverse("home"), headers={"accept-language": "hi"})

    assert '<html lang="en-IN">' in response.content.decode()


def test_the_offline_page_has_no_switcher(hindi_on, client: Client) -> None:
    page = client.get(reverse("pwa_offline")).content.decode()

    assert FORM_START not in page
    assert "हिन्दी" not in page
