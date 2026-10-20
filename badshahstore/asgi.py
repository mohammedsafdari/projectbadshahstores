"""
ASGI config for Badshah Store.

For production, set DJANGO_SETTINGS_MODULE to badshahstore.settings.prod
before loading the ASGI application.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "badshahstore.settings.dev")

application = get_asgi_application()
