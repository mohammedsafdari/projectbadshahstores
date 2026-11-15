"""The session clean-up job, its beat entry and the task registration (D94)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.sessions.models import Session
from django.utils import timezone

from apps.core.tasks import clear_expired_sessions
from badshahstore.celery import app


def _session(session_key: str, *, expires_in_days: int) -> Session:
    """Store one session row that expires the given number of days from now.

    A negative number stores a session that has already expired.
    """
    return Session.objects.create(
        session_key=session_key,
        session_data="",
        expire_date=timezone.now() + timedelta(days=expires_in_days),
    )


@pytest.mark.django_db
def test_expired_session_is_deleted() -> None:
    expired = _session("expired-session-key", expires_in_days=-1)
    expired_pk = expired.pk

    clear_expired_sessions()

    assert not Session.objects.filter(pk=expired_pk).exists()


@pytest.mark.django_db
def test_live_session_is_kept() -> None:
    live = _session("live-session-key", expires_in_days=1)
    live_pk = live.pk

    clear_expired_sessions()

    assert Session.objects.filter(pk=live_pk).exists()


@pytest.mark.django_db
def test_beat_entry_runs_at_0350(settings) -> None:
    entry = settings.CELERY_BEAT_SCHEDULE["clear-expired-sessions"]

    assert entry["task"] == "apps.core.tasks.clear_expired_sessions"
    assert entry["schedule"].hour == {3}
    assert entry["schedule"].minute == {50}


def test_both_tasks_are_registered_with_celery() -> None:
    # autodiscover_tasks() is lazy: it imports each installed app's tasks module
    # the first time the app is finalised, so the test asks the loader to import
    # them by hand and then reads the registry of task names.
    app.loader.import_default_modules()

    assert "apps.core.tasks.clear_expired_sessions" in app.tasks
    assert "apps.cart.tasks.delete_stale_saved_carts" in app.tasks
