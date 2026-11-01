"""Image helpers for the catalog: thumbnails and medium sizes (Slice 23b/25)."""

from __future__ import annotations

import logging
from io import BytesIO
from typing import BinaryIO

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

THUMBNAIL_MAX_SIDE = 400
MEDIUM_MAX_SIDE = 900
THUMBNAIL_QUALITY = 80
MEDIUM_QUALITY = 80


def create_image(source: BinaryIO, max_side: int, quality: int) -> ContentFile | None:
    """Build a WebP image at most ``max_side`` px on the longest side.

    The original is never lost: a failed conversion only logs a warning.
    """
    try:
        source.seek(0)
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)  # type: ignore[assignment]
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA") if "A" in image.getbands() else image.convert("RGB")  # type: ignore[assignment]
            image.thumbnail((max_side, max_side))
            buffer = BytesIO()
            image.save(buffer, format="WEBP", quality=quality)
            buffer.seek(0)
            return ContentFile(buffer.read())
    except Exception:
        logger.warning("Could not create an image size.", exc_info=True)
        return None


def create_thumbnail(source: BinaryIO) -> ContentFile | None:
    return create_image(source, THUMBNAIL_MAX_SIDE, THUMBNAIL_QUALITY)


def create_medium(source: BinaryIO) -> ContentFile | None:
    return create_image(source, MEDIUM_MAX_SIDE, MEDIUM_QUALITY)
