from __future__ import annotations

import io

import pytest

pytest.importorskip("PIL")
from PIL import Image
from src.media import read_discord_image, validate_image_bytes


def _image_bytes(image_format: str = "PNG", color: str = "red") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (16, 12), color).save(output, format=image_format)
    return output.getvalue()


class _Attachment:
    def __init__(self, data: bytes, *, size: int | None = None, content_type: str | None = None):
        self._data = data
        self.size = len(data) if size is None else size
        self.content_type = content_type
        self.read_calls = 0

    async def read(self) -> bytes:
        self.read_calls += 1
        return self._data


@pytest.mark.asyncio
async def test_discord_image_attachment_is_decoded_and_mime_detected() -> None:
    data = _image_bytes("PNG")
    attachment = _Attachment(data, content_type="image/png")
    result = await read_discord_image(attachment)
    assert result.data == data
    assert result.mime_type == "image/png"
    assert attachment.read_calls == 1


@pytest.mark.asyncio
async def test_rejects_large_advertised_attachment_before_reading() -> None:
    attachment = _Attachment(b"ignored", size=100, content_type="image/png")
    with pytest.raises(ValueError, match="larger"):
        await read_discord_image(attachment, max_bytes=50)
    assert attachment.read_calls == 0


@pytest.mark.asyncio
async def test_rejects_actual_oversize_spoofed_mime_and_bad_image() -> None:
    with pytest.raises(ValueError, match="larger"):
        await read_discord_image(_Attachment(_image_bytes(), size=1), max_bytes=30)
    with pytest.raises(ValueError, match="does not match"):
        await read_discord_image(_Attachment(_image_bytes("PNG"), content_type="image/jpeg"))
    with pytest.raises(ValueError, match="valid supported image"):
        await read_discord_image(
            _Attachment(b"\x89PNG\r\n\x1a\n" + b"bad", content_type="image/png")
        )


@pytest.mark.parametrize(
    ("image_format", "mime_type"),
    [("JPEG", "image/jpeg"), ("WEBP", "image/webp"), ("GIF", "image/gif")],
)
def test_validates_supported_image_formats(image_format: str, mime_type: str) -> None:
    assert validate_image_bytes(_image_bytes(image_format), claimed_mime=mime_type) == mime_type


def test_rejects_unsupported_vector_image() -> None:
    with pytest.raises(ValueError, match="valid supported image"):
        validate_image_bytes(b"<svg><script>alert(1)</script></svg>")
