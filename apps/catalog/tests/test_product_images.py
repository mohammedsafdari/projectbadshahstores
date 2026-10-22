"""Tests for the ProductImage model."""

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.catalog.models import ProductImage
from apps.catalog.validators import MAX_IMAGE_BYTES

from .factories import ProductFactory, ProductImageFactory

# Tests below touch the database.
pytestmark = pytest.mark.django_db


def _jpg_image_bytes(size=(10, 10)):
    """Return bytes of a tiny real JPEG for invalid-image tests."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size).save(buffer, format="JPEG")
    return buffer.getvalue()


def _fake_jpg_bytes():
    """Return plain text bytes saved under a .jpg name."""
    return b"not a real image at all"


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
    extension = "PHOTO.JPG"
    # The upload_to function needs instance.product_id to exist, so set it on
    # the build and then ask the field to produce the storage name.
    image = ProductImageFactory.build(
        product=product,
        image=SimpleUploadedFile(name=extension, content=_jpg_image_bytes()),
    )
    # Django's upload_to is called by FieldFile.save() via field.generate_filename(),
    # so call that directly to get the stored name without saving to disk.
    image.image.instance = image
    image.image.instance.product_id = product_id
    stored = image.image.field.generate_filename(image.image.instance, extension)

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
        image=SimpleUploadedFile(name="fake.jpg", content=_fake_jpg_bytes()),
    )

    with pytest.raises(ValidationError) as excinfo:
        image.full_clean()
    assert "image" in excinfo.value.message_dict


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
    assert "image" in excinfo.value.message_dict


def test_full_clean_rejects_oversized_image():
    product = ProductFactory()
    # The size validator runs before content validation, so use deterministic
    # bytes larger than the limit instead of a highly compressible JPEG.
    image = ProductImageFactory.build(
        product=product,
        image=SimpleUploadedFile(
            name="big.jpg",
            content=b"x" * (MAX_IMAGE_BYTES + 1),
        ),
    )

    with pytest.raises(ValidationError) as excinfo:
        image.full_clean()
    assert "image" in excinfo.value.message_dict


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
