"""
Badshah Store — Django project package.

Importing the Celery app here ensures the app registry is populated when
Django starts (e.g. when the dev server or a worker imports the package).
"""

from .celery import app as celery_app

__all__ = ("celery_app",)
