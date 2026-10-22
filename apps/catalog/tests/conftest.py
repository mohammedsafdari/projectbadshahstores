"""
Pytest configuration for catalog tests.

conftest.py files live next to the tests that use them and pytest loads
them automatically. Any fixture defined in a conftest.py is available to
all tests in that directory and its subdirectories, without importing the file.

An *autouse* fixture is applied to every test in its scope automatically, with
no need to name it as a test argument. Here we use it so every catalog test
writes uploaded media into pytest's temporary directory instead of the real
media/ folder.
"""

import pytest


@pytest.fixture(autouse=True)
def _media_root_on_tmp_path(tmp_path, settings):
    # Point MEDIA_ROOT at pytest's tmp_path so tests never touch the real media/ folder.
    settings.MEDIA_ROOT = tmp_path
