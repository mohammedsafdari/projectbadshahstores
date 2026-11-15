"""The files of the Tailwind build (D93).

The build itself cannot run in the tests: the CLI is a binary the owner and CI
run, not a Python command. What can be checked is that the pieces agree — the
config holds the same colours and font stack the CDN was given, the source file
has the three directives, every static name a template writes exists, the built
file is never committed, and the image and CI pin the same checksum.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

BASE_DIR = Path(settings.BASE_DIR)
TAILWIND_CONFIG = BASE_DIR / "tailwind.config.js"
TAILWIND_SOURCE = BASE_DIR / "static" / "src" / "tailwind.css"
TAILWIND_VERSION = "3.4.17"
TAILWIND_CHECKSUM = "7d24f7fa191d2193b78cd5f5a42a6093e14409521908529f42d80b11fde1f1d4"

BRAND_COLOURS = ("#fef7ed", "#fdebd0", "#ea580c", "#c2410c", "#9a3412")
CONTENT_GLOBS = (
    "./templates/**/*.html",
    "./apps/**/templates/**/*.html",
    "./apps/**/*.py",
    "./static/js/**/*.js",
)
STATIC_REFERENCE = re.compile(r'{%\s*static\s+"([^"]+)"\s*%}')
# The one name no folder holds, because the Tailwind CLI writes it.
GENERATED_STATIC_FILES = {"css/app.css"}


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _template_files() -> list[Path]:
    root = BASE_DIR
    files = [path for folder in ("templates", "apps") for path in (root / folder).rglob("*.html")]
    return sorted(files)


def test_tailwind_config_keeps_the_brand_colours() -> None:
    config = _text(TAILWIND_CONFIG)

    assert "brand: {" in config
    for colour in BRAND_COLOURS:
        assert f'"{colour}"' in config


def test_tailwind_config_keeps_the_devanagari_font_last() -> None:
    config = _text(TAILWIND_CONFIG)

    start = config.index("sans: [")
    block = config[start : config.index("]", start)]
    # re.findall pulls the quoted entries out of the array in order.
    entries = re.findall(r'"[^"]*"', block)

    assert entries[-1] == '"Noto Sans Devanagari"'
    assert entries[0] == '"ui-sans-serif"'
    assert len(entries) == 8


def test_tailwind_config_scans_templates_apps_and_js() -> None:
    config = _text(TAILWIND_CONFIG)

    for pattern in CONTENT_GLOBS:
        assert f'"{pattern}"' in config


def test_tailwind_source_has_the_three_directives_and_x_cloak() -> None:
    source = _text(TAILWIND_SOURCE)

    assert "@tailwind base;" in source
    assert "@tailwind components;" in source
    assert "@tailwind utilities;" in source
    assert "[x-cloak] { display: none !important; }" in source


def test_every_static_reference_in_templates_exists() -> None:
    static_root = BASE_DIR / "static"
    missing: list[str] = []

    for path in _template_files():
        for reference in STATIC_REFERENCE.findall(_text(path)):
            if reference in GENERATED_STATIC_FILES:
                continue
            if not (static_root / reference).is_file():
                missing.append(f"{reference} in {path.name}")

    assert missing == []


def test_app_css_is_gitignored() -> None:
    lines = [line.strip() for line in _text(BASE_DIR / ".gitignore").splitlines()]

    assert "static/css/app.css" in lines


def test_dockerfile_pins_the_tailwind_version_and_checksum() -> None:
    dockerfile = _text(BASE_DIR / "docker" / "Dockerfile")

    assert f"/v{TAILWIND_VERSION}/tailwindcss-linux-x64" in dockerfile
    assert f"sha256:{TAILWIND_CHECKSUM}" in dockerfile
    assert "chmod 755 /usr/local/bin/tailwindcss" in dockerfile
    # The image does not copy the source, so the CLI is all it holds.
    assert "COPY . ." not in dockerfile


def test_ci_builds_tailwind_with_the_same_checksum() -> None:
    workflow = _text(BASE_DIR / ".github" / "workflows" / "ci.yml")

    assert f"/v{TAILWIND_VERSION}/tailwindcss-linux-x64" in workflow
    assert TAILWIND_CHECKSUM in workflow
    assert "sha256sum -c" in workflow
    assert "--minify" in workflow
    # The step fails when the built file is empty or lacks the two proofs.
    assert "test -s static/css/app.css" in workflow
    assert "bg-brand-500" in workflow
    assert "x-cloak" in workflow
