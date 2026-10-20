"""Tests for the core health endpoint."""

import json

import pytest
from django.db.utils import OperationalError
from django.test import Client
from unittest.mock import patch

pytestmark = pytest.mark.django_db


def test_health_get_returns_ok_json(client):
    response = client.get("/health/")
    assert response.status_code == 200
    assert json.loads(response.content) == {"status": "ok"}


def test_health_get_has_no_store_cache_control(client):
    response = client.get("/health/")
    assert response["Cache-Control"] == "no-store"


def test_health_post_returns_405(client):
    response = client.post("/health/")
    assert response.status_code == 405


def test_health_head_returns_200_with_empty_body(client):
    response = client.head("/health/")
    assert response.status_code == 200
    assert response.content == b""


def test_health_returns_503_when_database_failed(client):
    with patch("apps.core.views.connection") as connection_mock:
        cursor_mock = connection_mock.cursor.return_value.__enter__.return_value
        cursor_mock.execute.side_effect = OperationalError("database not reachable")
        response = client.get("/health/")
    assert response.status_code == 503
    assert json.loads(response.content) == {"status": "error"}
    assert "database not reachable" not in response.content.decode("utf-8")
