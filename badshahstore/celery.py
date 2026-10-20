"""
Badshah Store — Celery application.

Configured from Django settings under the CELERY_ namespace, with task
autodiscovery off the installed apps. Import this once at startup so the
app registry is populated before any worker/beat process handles a task.
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "badshahstore.settings.dev")

app = Celery("badshahstore")

app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
