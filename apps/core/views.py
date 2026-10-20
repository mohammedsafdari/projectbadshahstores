"""Core app views."""

import logging

from django.db import connection
from django.db.utils import OperationalError
from django.http import HttpResponse, HttpResponseNotAllowed

logger = logging.getLogger(__name__)


def health(request):
    """Lightweight liveness/readiness probe for Docker and CI.

    GET and HEAD only. No login, no cache, no extra information in the body.
    """
    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        logger.exception("Health check database probe failed")
        response = HttpResponse(
            '{"status": "error"}',
            content_type="application/json",
            status=503,
        )
    else:
        response = HttpResponse(
            '{"status": "ok"}',
            content_type="application/json",
            status=200,
        )

    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
