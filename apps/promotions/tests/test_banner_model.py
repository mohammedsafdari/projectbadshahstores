"""Tests for the banner model and its link rule (D84).

REVIEW CAREFULLY: a bad link or a broken image is a public home page problem.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from PIL import Image

from apps.catalog.validators import MAX_IMAGE_BYTES
from apps.promotions.models import BANNER_LINK_MESSAGE, Banner, banner_image_upload_path
from apps.promotions.tests.helpers import make_banner, png_bytes

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _media_root_on_tmp_path(tmp_path, settings):
    """Every upload in this module goes to the temporary folder, never media/."""
    settings.MEDIA_ROOT = tmp_path


def _with_link(link: str) -> Banner:
    """An unsaved banner with a link; its image and alt text are valid."""
    return Banner(
        title="Offer",
        alt_text="Offer",
        link_url=link,
        image=SimpleUploadedFile("banner.png", png_bytes(10, 10), content_type="image/png"),
    )


def _unsaved_with_image(upload: SimpleUploadedFile) -> Banner:
    return Banner(title="Offer", alt_text="Offer", image=upload)


# ---------------------------------------------------------------------------
# The dates
# ---------------------------------------------------------------------------
def test_an_end_before_the_start_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_banner(starts_at=NOW, ends_at=NOW - timedelta(hours=1))


def test_an_end_at_the_start_is_refused() -> None:
    # The end must be strictly later, so a banner is never shown for no time.
    with pytest.raises(IntegrityError), transaction.atomic():
        make_banner(starts_at=NOW, ends_at=NOW)


def test_an_end_after_the_start_is_allowed() -> None:
    banner = make_banner(starts_at=NOW, ends_at=NOW + timedelta(days=1))

    assert banner.pk is not None


def test_no_dates_at_all_is_allowed() -> None:
    banner = make_banner()

    assert banner.starts_at is None
    assert banner.ends_at is None


@pytest.mark.parametrize("field", ["starts_at", "ends_at"])
def test_one_date_alone_is_allowed(field: str) -> None:
    banner = make_banner(**{field: NOW})

    assert getattr(banner, field) == NOW


# ---------------------------------------------------------------------------
# The link rule
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("link", ["", "/offers", "/p/rice/", "https://example.com/a"])
def test_a_good_link_is_accepted(link: str) -> None:
    _with_link(link).full_clean()


@pytest.mark.parametrize(
    "link",
    [
        "http://example.com",
        "javascript:alert(1)",
        "//evil.com",
        "/a b",
        "/a\\b",
        "data:text/html",
        "https://",
    ],
)
def test_a_bad_link_is_refused_with_the_sentence(link: str) -> None:
    with pytest.raises(ValidationError) as caught:
        _with_link(link).full_clean()

    assert caught.value.message_dict["link_url"] == [str(BANNER_LINK_MESSAGE)]


# ---------------------------------------------------------------------------
# The text and the image
# ---------------------------------------------------------------------------
def test_the_alt_text_is_required() -> None:
    banner = make_banner()
    banner.alt_text = ""

    with pytest.raises(ValidationError) as caught:
        banner.full_clean(exclude=["image"])

    assert "alt_text" in caught.value.message_dict


def test_a_pdf_is_refused() -> None:
    banner = _unsaved_with_image(
        SimpleUploadedFile("offer.pdf", b"%PDF-1.4", content_type="application/pdf")
    )

    with pytest.raises(ValidationError) as caught:
        banner.full_clean()

    assert "image" in caught.value.message_dict


def test_a_gif_is_refused() -> None:
    buffer = BytesIO()
    Image.new("RGB", (10, 10)).save(buffer, format="GIF")
    banner = _unsaved_with_image(
        SimpleUploadedFile("offer.gif", buffer.getvalue(), content_type="image/gif")
    )

    with pytest.raises(ValidationError) as caught:
        banner.full_clean()

    assert "image" in caught.value.message_dict


def test_text_renamed_to_jpg_is_refused() -> None:
    banner = _unsaved_with_image(
        SimpleUploadedFile("offer.jpg", b"this is not an image", content_type="image/jpeg")
    )

    with pytest.raises(ValidationError) as caught:
        banner.full_clean()

    assert "image" in caught.value.message_dict


def test_a_file_over_two_megabytes_is_refused() -> None:
    banner = _unsaved_with_image(
        SimpleUploadedFile(
            "offer.jpg",
            b"\xff" * (MAX_IMAGE_BYTES + 1),
            content_type="image/jpeg",
        )
    )

    with pytest.raises(ValidationError) as caught:
        banner.full_clean()

    assert "image" in caught.value.message_dict


def test_a_wrong_extension_is_refused() -> None:
    banner = _unsaved_with_image(
        SimpleUploadedFile("offer.txt", png_bytes(10, 10), content_type="image/png")
    )

    with pytest.raises(ValidationError) as caught:
        banner.full_clean()

    assert "image" in caught.value.message_dict


def test_the_upload_path_is_random_and_under_banners() -> None:
    banner = Banner(title="Offer", alt_text="Offer")

    first = banner_image_upload_path(banner, "My Photo.JPG")
    second = banner_image_upload_path(banner, "My Photo.JPG")

    assert re.fullmatch(r"banners/[0-9a-f]{32}\.jpg", first)
    assert first != second


def test_the_image_size_is_stored() -> None:
    banner = make_banner(
        image=SimpleUploadedFile("wide.png", png_bytes(900, 300), content_type="image/png")
    )

    assert (banner.width, banner.height) == (900, 300)


# ---------------------------------------------------------------------------
# The WebP display copy
# ---------------------------------------------------------------------------
def test_a_webp_display_copy_is_made_for_a_new_image() -> None:
    banner = make_banner()

    assert banner.display.name.startswith("banners/display/")
    assert banner.display.name.endswith(".webp")
    with Image.open(Path(banner.display.path)) as copy:
        assert copy.format == "WEBP"


def test_the_display_copy_is_at_most_1200_pixels_on_its_longest_side() -> None:
    banner = make_banner(
        image=SimpleUploadedFile("tall.png", png_bytes(1600, 2000), content_type="image/png")
    )

    with Image.open(Path(banner.display.path)) as copy:
        assert max(copy.size) == 1200


def test_a_new_display_copy_is_made_when_the_image_changes() -> None:
    banner = make_banner()
    first = banner.display.name

    banner.image = SimpleUploadedFile("new.png", png_bytes(600, 300), content_type="image/png")
    banner.save()
    banner.refresh_from_db()

    assert banner.display.name != first
    assert (banner.width, banner.height) == (600, 300)


def test_no_display_copy_is_made_when_the_image_is_unchanged() -> None:
    banner = make_banner()
    first = banner.display.name

    banner.title = "Another title"
    banner.save()
    banner.refresh_from_db()

    assert banner.display.name == first


def test_a_failing_resize_is_swallowed_with_a_warning(monkeypatch, caplog) -> None:
    def explode(*args, **kwargs):
        raise RuntimeError("Pillow cannot resize this")

    monkeypatch.setattr("apps.catalog.images.create_image", explode)
    with caplog.at_level("WARNING", logger="apps.promotions.models"):
        banner = make_banner()

    # The original image is still there, so the banner still shows.
    assert not banner.display
    assert "Could not create a banner display image" in caplog.text
