"""Tests for the ProductImage model."""

import io

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.catalog.models import ProductImage, product_image_upload_path
from apps.catalog.tests.factories import ProductImageFactory
from apps.catalog.validators import MAX_IMAGE_BYTES

from .factories import ProductFactory


def _uploaded_fake_jpg():
    """Return a SimpleUploadedFile whose bytes are not a real image."""
    return SimpleUploadedFile(name="fake.jpg", content=b"not a real image at all")


# Tests below touch the database.
pytestmark = pytest.mark.django_db


def _jpg_image_bytes(size=(10, 10)):
    """Return bytes of a tiny real JPEG for invalid-image tests."""
    buffer = io.BytesIO()
    Image.new("RGB", size).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_image_is_linked_to_product():
    product = ProductFactory()
    image = ProductImageFactory(product=product)

    assert image.product == product
    assert list(product.images.all()) == [image]


def test_images_are_ordered_by_sort_order_then_id():
    product = ProductFactory()
    first = ProductImageFactory.create(product=product, sort_order=2)
    second = ProductImageFactory.create(product=product, sort_order=1)
    third = ProductImageFactory.create(product=product, sort_order=1)

    ordered = list(product.images.all())
    assert ordered == [second, third, first]
    assert ordered[0].id < ordered[1].id


def test_upload_to_prefix_and_random_filename():
    product = ProductFactory()
    product_id = product.pk
    # Build a ProductImage instance and call upload_to directly. This tests the
    # function itself without creating an upload file or touching storage.
    image = ProductImageFactory.build(product=product)
    stored = product_image_upload_path(image, "PHOTO.JPG")

    expected_prefix = f"products/{product_id}/"
    assert stored.startswith(expected_prefix)
    assert stored.endswith(".jpg")

    # The file name after the prefix is 32 hex characters (a uuid hex) + ".jpg".
    name_part = stored[len(expected_prefix) :]
    hex_part, ext = name_part.rsplit(".", 1)
    assert len(hex_part) == 32
    assert all(ch in "0123456789abcdef" for ch in hex_part)
    assert ext == "jpg"


def test_width_and_height_are_filled_in_after_save():
    product = ProductFactory()
    image = ProductImageFactory(product=product)

    image.refresh_from_db()
    assert image.width is not None
    assert image.height is not None
    assert image.width > 0
    assert image.height > 0


def test_full_clean_rejects_fake_image():
    product = ProductFactory()
    image = ProductImageFactory.build(
        product=product,
        image=_uploaded_fake_jpg(),
    )

    with pytest.raises(ValidationError) as excinfo:
        image.full_clean()
    # content validation raises invalid_image; check the code directly.
    errors = excinfo.value.error_dict.get("image", excinfo.value.error_dict.get("image", []))
    codes = [err.code for err in errors]
    assert "invalid_image" in codes


def test_full_clean_rejects_disallowed_extension():
    product = ProductFactory()
    image = ProductImageFactory.build(
        product=product,
        image=SimpleUploadedFile(
            name="photo.gif",
            content=_jpg_image_bytes(),
        ),
    )

    with pytest.raises(ValidationError) as excinfo:
        image.full_clean()
    errors = excinfo.value.error_dict.get("image", excinfo.value.error_dict.get("image", []))
    codes = [err.code for err in errors]
    # extension validation runs first; if it did not fire, check the whole
    # list so we do not fail when another validator raises for a different reason.
    assert "invalid_extension" in codes


def test_full_clean_rejects_oversized_image():
    product = ProductFactory()
    # Use deterministic bytes larger than the limit so the size error is
    # predictable; this also keeps the test away from generating huge JPEGs.
    image = ProductImageFactory.build(
        product=product,
        image=SimpleUploadedFile(
            name="big.jpg",
            content=b"x" * (MAX_IMAGE_BYTES + 1),
        ),
    )

    with pytest.raises(ValidationError) as excinfo:
        image.full_clean()
    errors = excinfo.value.error_dict.get("image", excinfo.value.error_dict.get("image", []))
    codes = [err.code for err in errors]
    assert "image_too_large" in codes


def test_upload_to_handles_filename_without_extension():
    product = ProductFactory()
    product_id = product.pk
    # A filename without a dot must not crash and must not add an extension.
    image = ProductImageFactory.build(product=product)
    stored = product_image_upload_path(image, "photo")

    assert stored.startswith(f"products/{product_id}/")
    assert "." not in stored

    name_part = stored[len(f"products/{product_id}/") :]
    assert len(name_part) == 32
    assert all(ch in "0123456789abcdef" for ch in name_part)


def test_alt_text_may_be_blank():
    product = ProductFactory()
    image = ProductImageFactory(product=product, alt_text="")
    assert image.alt_text == ""


def test_deleting_product_deletes_its_images():
    product = ProductFactory()
    image = ProductImageFactory(product=product)
    product_id = image.product_id

    product.delete()
    assert ProductImage.objects.filter(product_id=product_id).count() == 0
