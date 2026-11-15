"""The five placeholder icons in static/img/pwa/ (D91).

REVIEW CAREFULLY: the owner supplies these files, and the agent never creates,
edits or replaces one. A wrong size makes the phone show a cropped or scaled
icon, and a transparent corner makes a maskable icon look cut off.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from PIL import Image

# A guard against a huge file dropped in by mistake, not a size target: a real
# logo at 512 pixels passes 100 KiB easily (raised from 20 KiB in 50f1).
MAX_ICON_BYTES = 100 * 1024

ICON_NAMES = (
    "icon-192.png",
    "icon-512.png",
    "icon-512-maskable.png",
    "apple-touch-icon-180.png",
    "favicon-32.png",
)

CORNERS = ((0, 0), (-1, 0), (0, -1), (-1, -1))


def _icon_path(name: str) -> Path:
    return Path(settings.BASE_DIR) / "static" / "img" / "pwa" / name


def _icon(name: str) -> Image.Image:
    path = _icon_path(name)
    assert path.is_file(), f"missing icon: {path}"
    return Image.open(path)


def _assert_square_png(name: str, size: int) -> None:
    image = _icon(name)

    assert image.format == "PNG"
    assert image.size == (size, size)


def _assert_corners_opaque(name: str) -> None:
    # The four corners are where a phone crops a maskable icon, so a transparent
    # one would show the wallpaper through the logo.
    rgba = _icon(name).convert("RGBA")
    for corner in CORNERS:
        assert rgba.getpixel(corner)[3] == 255, ("transparent corner", name, corner)


def test_icon_192_is_a_192_square_png() -> None:
    _assert_square_png("icon-192.png", 192)


def test_icon_512_is_a_512_square_png() -> None:
    _assert_square_png("icon-512.png", 512)


def test_icon_512_maskable_is_a_512_square_png() -> None:
    _assert_square_png("icon-512-maskable.png", 512)


def test_apple_touch_icon_is_a_180_square_png() -> None:
    _assert_square_png("apple-touch-icon-180.png", 180)


def test_favicon_is_a_32_square_png() -> None:
    _assert_square_png("favicon-32.png", 32)


def test_maskable_icon_is_opaque_in_the_corners() -> None:
    _assert_corners_opaque("icon-512-maskable.png")


def test_apple_touch_icon_is_opaque_in_the_corners() -> None:
    # iOS puts this one on a white square, so a transparent corner shows as a
    # pale ring around the logo.
    _assert_corners_opaque("apple-touch-icon-180.png")


def test_each_icon_is_under_100_kb() -> None:
    for name in ICON_NAMES:
        path = _icon_path(name)
        assert path.is_file(), f"missing icon: {path}"
        assert path.stat().st_size < MAX_ICON_BYTES, (
            name,
            path.stat().st_size,
            MAX_ICON_BYTES,
        )
