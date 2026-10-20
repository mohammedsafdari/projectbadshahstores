"""
WSGI config for Badshah Store.

For production, set DJANGO_SETTINGS_MODULE to badshahstore.settings.prod
before loading the WSGI app.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "badshahstore.settings.dev")

application = get_wsgi_application()
