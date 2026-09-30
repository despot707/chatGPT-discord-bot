"""Safe ingestion of Discord image attachments without writing user files to disk."""

from __future__ import annotations

import io
import warnings
from typing import Any

from .providers import ImageInput

_MAX_PIXELS = 20_000_000
_FORMAT_MIME = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}
_MIME_FORMAT = {mime: image_format for image_format, mime in _FORMAT_MIME.items()}


async def read_discord_image(attachment: Any, *, max_bytes: int = 5_000_000) -> ImageInput:
    """Read and decode one Discord attachment after checking its advertised size."""
    if max_bytes < 1:
        raise ValueError("Image size limit must be positive.")
    advertised_size = getattr(attachment, "size", None)
    if isinstance(advertised_size, int) and (advertised_size < 0 or advertised_size > max_bytes):
        raise ValueError("That image is larger than the allowed upload size.")
    claimed_mime = getattr(attachment, "content_type", None)
    if claimed_mime is not None:
        claimed_mime = str(claimed_mime).split(";", 1)[0].strip().lower()
        if claimed_mime not in _MIME_FORMAT:
            raise ValueError("Upload a PNG, JPEG, WebP, or GIF image.")
    try:
        data = await attachment.read()
    except Exception as exc:
        raise ValueError("Discord could not provide that image attachment.") from exc
    if not isinstance(data, bytes) or not data:
        raise ValueError("That image attachment was empty or invalid.")
    if len(data) > max_bytes:
        raise ValueError("That image is larger than the allowed upload size.")
    mime_type = validate_image_bytes(data, claimed_mime=claimed_mime, max_bytes=max_bytes)
    return ImageInput(data=data, mime_type=mime_type)


def validate_image_bytes(
    data: bytes, *, claimed_mime: str | None = None, max_bytes: int = 5_000_000
) -> str:
    """Verify supported raster bytes with Pillow and enforce bounded dimensions."""
    if not isinstance(data, bytes) or not data or len(data) > max_bytes:
        raise ValueError("Image data is empty or exceeds the size limit.")
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:
        raise RuntimeError("Pillow is required to safely validate uploaded images.") from exc

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                image_format = image.format
                if image_format not in _FORMAT_MIME:
                    raise ValueError("Upload a PNG, JPEG, WebP, or GIF image.")
                if (
                    image.width <= 0
                    or image.height <= 0
                    or image.width * image.height > _MAX_PIXELS
                ):
                    raise ValueError("That image has dimensions that are too large to process.")
                mime_type = _FORMAT_MIME[image_format]
                if claimed_mime and claimed_mime != mime_type:
                    raise ValueError("The image content does not match its declared file type.")
                image.verify()
            # verify() checks the container structure but does not decode pixels.
            with Image.open(io.BytesIO(data)) as image:
                image.seek(0)
                if image.width * image.height > _MAX_PIXELS:
                    raise ValueError("That image has dimensions that are too large to process.")
                image.load()
                if image.width <= 0 or image.height <= 0:
                    raise ValueError("That image is invalid.")
        return mime_type
    except ValueError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("That attachment is not a valid supported image.") from exc
