"""A Django ``{# ... #}`` comment works on one line only (Slice 52 fix).

Django ends a ``{# ... #}`` comment at the end of its line. A comment written
across two lines is therefore not a comment at all: the template engine prints
it as text on the page, and no other test notices, because the page is still
valid HTML and every value is still correct. Five templates had done this before
Slice 52 and the cart page showed two of them; this test is what keeps them from
coming back.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings

# The two folders that hold the project's templates.
TEMPLATE_FOLDERS = ("templates", "apps")


def _template_files() -> list[Path]:
    """Every .html template the project ships, in a stable order."""
    root = Path(settings.BASE_DIR)
    files = [path for folder in TEMPLATE_FOLDERS for path in (root / folder).rglob("*.html")]
    return sorted(files)


def test_every_template_comment_fits_on_one_line() -> None:
    offenders: list[str] = []
    for path in _template_files():
        text = path.read_text(encoding="utf-8")
        # enumerate gives the line number and the text together, 1-based.
        for number, line in enumerate(text.splitlines(), start=1):
            if "{#" in line and "#}" not in line:
                relative = path.relative_to(Path(settings.BASE_DIR)).as_posix()
                offenders.append(f"{relative}:{number}: {line.strip()}")

    assert offenders == []
