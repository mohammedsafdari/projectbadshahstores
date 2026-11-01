"""Image helpers for the catalog: thumbnails (Slice 23b)."""

from __future__ import annotations

import logging
from io import BytesIO
from typing import BinaryIO

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

THUMBNAIL_MAX_SIDE = 400
THUMBNAIL_QUALITY = 80


def create_thumbnail(source: BinaryIO) -> ContentFile | None:
    """Build a WebP thumbnail, or None if it cannot be made.

    The original is never lost: a failed thumbnail only logs a warning.
    """
    try:
        source.seek(0)
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)  # type: ignore[assignment]
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA") if "A" in image.getbands() else image.convert("RGB")  # type: ignore[assignment]
            # thumbnail() never enlarges the image.
            image.thumbnail((THUMBNAIL_MAX_SIDE, THUMBNAIL_MAX_SIDE))
            buffer = BytesIO()
            image.save(buffer, format="WEBP", quality=THUMBNAIL_QUALITY)
            buffer.seek(0)
            return ContentFile(buffer.read())
    except Exception:
        logger.warning("Could not create a thumbnail.", exc_info=True)
        return None
