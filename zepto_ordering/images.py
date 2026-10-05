"""Prepare uploaded list photos for Claude."""

from __future__ import annotations

import io

from PIL import Image, ImageOps, UnidentifiedImageError

# Claude downsizes anything larger, so sending more only adds upload time.
MAX_EDGE_PIXELS = 1568
JPEG_QUALITY = 85
# Larger than any phone camera, small enough to keep decoding memory bounded.
MAX_PHOTO_PIXELS = 120_000_000


class InvalidImageError(Exception):
    """Raised when an upload is not a readable image."""


def normalize_photo(data: bytes) -> bytes:
    """Return the photo as an upright JPEG no larger than MAX_EDGE_PIXELS on its long edge.

    This is CPU-bound, so async callers should run it in a thread.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if width * height > MAX_PHOTO_PIXELS:
                raise InvalidImageError("That photo is too large. Try a smaller one.")
            # For JPEGs, decode at a reduced scale instead of full size, which saves
            # most of the memory and time for big camera photos.
            image.draft("RGB", (MAX_EDGE_PIXELS * 2, MAX_EDGE_PIXELS * 2))
            # Phone cameras store rotation in EXIF instead of rotating the pixels.
            upright = ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as error:
        raise InvalidImageError("Upload a JPEG, PNG or WebP photo.") from error

    upright.thumbnail((MAX_EDGE_PIXELS, MAX_EDGE_PIXELS))
    output = io.BytesIO()
    upright.save(output, format="JPEG", quality=JPEG_QUALITY)
    return output.getvalue()
