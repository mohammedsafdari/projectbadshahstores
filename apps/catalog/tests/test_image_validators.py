"""Tests for the product image upload validators (no database needed)."""

import io
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.catalog import validators


def make_image_file(name="photo.jpg", image_format="JPEG", size=(10, 10)):
    """Build a tiny real image in memory, as if it had just been uploaded."""
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format=image_format)
    return SimpleUploadedFile(name, buffer.getvalue())


def run_all(upload):
    """Run the three validators in the same order the model field will."""
    validators.validate_image_extension(upload)
    validators.validate_image_size(upload)
    validators.validate_image_content(upload)


@pytest.mark.parametrize(
    ("name", "image_format"),
    [("a.jpg", "JPEG"), ("a.jpeg", "JPEG"), ("a.png", "PNG"), ("a.webp", "WEBP")],
)
def test_allowed_images_pass(name, image_format):
    run_all(make_image_file(name, image_format))


def test_uppercase_extension_is_accepted():
    run_all(make_image_file("PHOTO.JPG", "JPEG"))


def test_extension_not_allowed_is_rejected():
    with pytest.raises(ValidationError) as exc_info:
        validators.validate_image_extension(make_image_file("a.gif", "GIF"))
    assert exc_info.value.code == "invalid_extension"


def test_file_exactly_at_size_limit_passes():
    upload = SimpleUploadedFile("a.jpg", b"x" * validators.MAX_IMAGE_BYTES)
    validators.validate_image_size(upload)


def test_file_over_size_limit_is_rejected():
    upload = SimpleUploadedFile("a.jpg", b"x" * (validators.MAX_IMAGE_BYTES + 1))
    with pytest.raises(ValidationError) as exc_info:
        validators.validate_image_size(upload)
    assert exc_info.value.code == "image_too_large"


def test_text_file_renamed_to_jpg_is_rejected():
    upload = SimpleUploadedFile("fake.jpg", b"this is not an image")
    with pytest.raises(ValidationError) as exc_info:
        validators.validate_image_content(upload)
    assert exc_info.value.code == "invalid_image"


def test_real_gif_renamed_to_jpg_is_rejected():
    upload = make_image_file("sneaky.jpg", "GIF")
    with pytest.raises(ValidationError) as exc_info:
        validators.validate_image_content(upload)
    assert exc_info.value.code == "invalid_image_format"


def test_image_with_too_long_side_is_rejected():
    # 8001 x 1 pixels is a tiny file, but beyond the allowed dimensions.
    upload = make_image_file("wide.png", "PNG", size=(validators.MAX_IMAGE_SIDE + 1, 1))
    with pytest.raises(ValidationError) as exc_info:
        validators.validate_image_content(upload)
    assert exc_info.value.code == "image_dimensions_too_large"


def test_file_is_rewound_after_checking():
    upload = make_image_file()
    validators.validate_image_content(upload)
    assert upload.tell() == 0


def test_already_stored_files_are_skipped():
    # A saved file has _committed=True. The validators must not open it.
    stored = SimpleNamespace(_committed=True, size=10**9)
    validators.validate_image_size(stored)
    validators.validate_image_content(stored)
