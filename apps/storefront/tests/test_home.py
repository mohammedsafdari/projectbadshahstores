"""Tests for the storefront home page."""

import re

import pytest
from django.contrib.staticfiles import finders

pytestmark = pytest.mark.django_db


def test_home_returns_200(client):
    response = client.get("/")
    assert response.status_code == 200


def test_home_contains_expected_template_pieces(client):
    response = client.get("/")
    html = response.content.decode("utf-8", "replace")

    assert '<html lang="en-IN">' in html
    assert "width=device-width, initial-scale=1" in html
    assert re.search(r'<main\b[^>]*\bid="content"', html)
    assert 'href="#content"' in html
    assert "hx-headers" in html
    assert "X-CSRFToken" in html
    assert "placeholder" in html.lower() or "#" in html


def test_home_does_not_expose_legal_details(client):
    response = client.get("/")
    html = response.content.decode("utf-8", "replace")
    assert "GSTIN" not in html
    assert "FSSAI" not in html


def test_vendored_static_files_are_findable():
    for path in [
        "vendor/htmx/htmx-2.0.4.min.js",
        "vendor/alpine/alpine-3.14.8.min.js",
    ]:
        found = finders.find(path)
        assert found, f"Static file not found: {path}"


def test_developer_check_not_rendered_when_debug_is_false(settings, client):
    settings.DEBUG = False
    response = client.get("/")
    html = response.content.decode("utf-8", "replace")
    assert "Developer check" not in html
