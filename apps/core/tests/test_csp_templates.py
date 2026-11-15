"""What the templates may contain under the Content-Security-Policy (D93).

A rule is only as good as the pages it covers, so these tests read every
template the project ships and refuse the things the policy would block: an
inline script without the nonce, an inline event handler, a `javascript:` link,
a script from any host but the payment checkout, and an off-site stylesheet.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

from apps.core.csp import PAYMENT_CHECKOUT_ORIGIN

# The two folders that hold the project's templates.
TEMPLATE_FOLDERS = ("templates", "apps")

SCRIPT_TAG = re.compile(r"<script\b[^>]*>", flags=re.IGNORECASE)
STYLESHEET_TAG = re.compile(r"<link\b[^>]*>", flags=re.IGNORECASE)
SRC_ATTRIBUTE = re.compile(r'src="([^"]*)"', flags=re.IGNORECASE)
HREF_ATTRIBUTE = re.compile(r'href="([^"]*)"', flags=re.IGNORECASE)
# A space before "on" is what makes it an attribute; x-on:click and hx-on are
# Alpine and htmx syntax and never match, because the name must be letters.
EVENT_HANDLER = re.compile(r"\son[a-z]+\s*=\s*[\"']", flags=re.IGNORECASE)

# An inline script may run only with the nonce, and a JSON-LD block is data.
ALLOWED_INLINE_SCRIPT = ("nonce=", 'type="application/ld+json"')


def _template_files() -> list[Path]:
    """Every .html template the project ships, in a stable order."""
    root = Path(settings.BASE_DIR)
    files = [path for folder in TEMPLATE_FOLDERS for path in (root / folder).rglob("*.html")]
    return sorted(files)


def _relative(path: Path) -> str:
    return path.relative_to(Path(settings.BASE_DIR)).as_posix()


def _offenders(problem: str, path: Path) -> str:
    """One report line, so a failure names the file and the text."""
    return f"{_relative(path)}: {problem}"


def test_no_inline_script_without_nonce_in_any_template() -> None:
    offenders: list[str] = []
    for path in _template_files():
        text = path.read_text(encoding="utf-8")
        for tag in SCRIPT_TAG.findall(text):
            if "src=" in tag.lower():
                continue
            if any(allowed in tag for allowed in ALLOWED_INLINE_SCRIPT):
                continue
            offenders.append(_offenders(tag, path))

    assert offenders == []


def test_no_inline_event_handler_attributes() -> None:
    offenders: list[str] = []
    for path in _template_files():
        for match in EVENT_HANDLER.finditer(path.read_text(encoding="utf-8")):
            offenders.append(_offenders(match.group(0).strip(), path))

    assert offenders == []


def test_no_javascript_urls() -> None:
    offenders: list[str] = []
    for path in _template_files():
        text = path.read_text(encoding="utf-8").lower()
        if "javascript:" in text:
            offenders.append(_relative(path))

    assert offenders == []


def test_only_the_razorpay_host_loads_external_scripts() -> None:
    offenders: list[str] = []
    for path in _template_files():
        for tag in SCRIPT_TAG.findall(path.read_text(encoding="utf-8")):
            for source in SRC_ATTRIBUTE.findall(tag):
                if not source.startswith(("http://", "https://")):
                    continue
                if source.startswith(PAYMENT_CHECKOUT_ORIGIN):
                    continue
                offenders.append(_offenders(source, path))

    assert offenders == []


def test_no_external_stylesheet_remains() -> None:
    offenders: list[str] = []
    for path in _template_files():
        for tag in STYLESHEET_TAG.findall(path.read_text(encoding="utf-8")):
            if 'rel="stylesheet"' not in tag.lower():
                continue
            for href in HREF_ATTRIBUTE.findall(tag):
                if href.startswith(("http://", "https://")):
                    offenders.append(_offenders(href, path))

    assert offenders == []


def test_base_and_staff_base_load_app_css_not_the_cdn() -> None:
    root = Path(settings.BASE_DIR)
    shop_base = (root / "templates" / "base.html").read_text(encoding="utf-8")
    staff_base = (root / "apps" / "orders" / "templates" / "staff" / "base.html").read_text(
        encoding="utf-8"
    )

    for text in (shop_base, staff_base):
        assert '{% static "css/app.css" %}' in text
        assert "cdn.tailwindcss.com" not in text
    # The staff base needs the static tag, because it links the stylesheet.
    assert re.search(r"{% load [^%]*\bstatic\b", staff_base) is not None


def test_offline_page_stays_free_of_external_loads() -> None:
    offline = (Path(settings.BASE_DIR) / "templates" / "pwa" / "offline.html").read_text(
        encoding="utf-8"
    )

    assert "<script" not in offline.lower()
    assert "<link" not in offline.lower()
    assert "http://" not in offline
    assert "https://" not in offline
    assert "@import" not in offline
