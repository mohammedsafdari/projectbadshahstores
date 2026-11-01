"""
Pytest configuration for storefront tests.
"""

import pytest


@pytest.fixture(autouse=True)
def _media_root_on_tmp_path(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
