"""Validators for uploaded product images (Slice 14).

Django calls a validator with the field's value. The validator returns
quietly when the value is fine, or raises ValidationError when it is not.
For an upload, the value is a FieldFile (a wrapper around the uploaded file).
"""

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.utils.translation import gettext_lazy as _
from PIL import Image

# Limits are plain constants so tests and docs can import them.
MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_IMAGE_SIDE = 8000  # pixels, longest side
ALLOWED_IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp"]
# The names Pillow uses for the real file format, whatever the file is called.
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}

# Built into Django. Case-insensitive, so "PHOTO.JPG" is accepted.
validate_image_extension = FileExtensionValidator(allowed_extensions=ALLOWED_IMAGE_EXTENSIONS)


def _is_already_stored(value) -> bool:
    """Return True for a file saved earlier (not a new upload).

    Stored files were checked when they were uploaded. Skipping them means
    editing an old row never touches the disk (the file may even be missing
    in a fresh dev container). ``_committed`` is a FieldFile attribute that
    is False only while a new upload is waiting to be saved. getattr() with
    a default returns False when the attribute does not exist at all.
    """
    return getattr(value, "_committed", False)


def validate_image_size(value) -> None:
    """Reject uploads larger than MAX_IMAGE_BYTES."""
    if _is_already_stored(value):
        return
    if value.size > MAX_IMAGE_BYTES:
        raise ValidationError(
            _("Image is too large. The maximum size is %(max_mb)s MB."),
            code="image_too_large",
            params={"max_mb": MAX_IMAGE_BYTES // (1024 * 1024)},
        )


def validate_image_content(value) -> None:
    """Check the real file content, not just the file name.

    A file called photo.jpg could be a GIF, a PDF or plain text. Pillow looks
    at the file header to find out what it really is.
    """
    if _is_already_stored(value):
        return
    try:
        value.seek(0)
        # Image.open reads only the header, so this is cheap and does not
        # decode the whole picture. "with ... as" closes the image afterwards.
        with Image.open(value) as image:
            image_format = image.format
            width, height = image.size
    except (OSError, Image.DecompressionBombError) as exc:
        # UnidentifiedImageError (not an image at all) is a kind of OSError.
        raise ValidationError(
            _("Upload a valid image file."),
            code="invalid_image",
        ) from exc
    finally:
        # Rewind so Django can still read and save the file afterwards.
        value.seek(0)

    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise ValidationError(
            _("Only JPEG, PNG and WebP images are allowed."),
            code="invalid_image_format",
        )
    if max(width, height) > MAX_IMAGE_SIDE:
        raise ValidationError(
            _("Image is too large. The longest side can be at most %(max_px)s pixels."),
            code="image_dimensions_too_large",
            params={"max_px": MAX_IMAGE_SIDE},
        )
