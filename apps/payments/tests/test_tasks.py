"""Tests for the payment Celery task and the one-off command (D77)."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from apps.payments import tasks

pytestmark = pytest.mark.django_db


def test_the_task_returns_the_service_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("apps.payments.tasks.expire_orders", lambda: 3)

    assert tasks.expire_unpaid_online_orders() == 3


def test_the_task_calls_the_service_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_expire() -> int:
        calls.append(1)
        return 0

    monkeypatch.setattr("apps.payments.tasks.expire_orders", fake_expire)

    tasks.expire_unpaid_online_orders()

    assert calls == [1]


def test_the_command_reports_the_cancelled_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.payments.management.commands.expire_unpaid_orders.expire_unpaid_online_orders",
        lambda: 2,
    )
    output = StringIO()

    call_command("expire_unpaid_orders", stdout=output)

    assert "Cancelled 2 unpaid online order(s)." in output.getvalue()
