"""Tests for avatar image downscaling."""

import io

from PIL import Image

from app.modules.storage.application.image_processing import (
    AVATAR_MAX_DIMENSION,
    resize_avatar,
)


def _png(width: int, height: int, color: tuple[int, int, int] = (100, 150, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def test_resize_avatar_downscales_and_keeps_aspect_ratio() -> None:
    original = _png(2000, 1500)
    out, mime = resize_avatar(original)

    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(out)) as result:
        # Longest side capped at the ceiling, aspect ratio preserved (2000x1500).
        assert result.size == (AVATAR_MAX_DIMENSION, 384)
    assert len(out) < len(original)


def test_resize_avatar_does_not_upscale_small_image() -> None:
    original = _png(100, 100)
    out, _ = resize_avatar(original)

    with Image.open(io.BytesIO(out)) as result:
        assert result.size == (100, 100)


def test_resize_avatar_flattens_transparency_to_jpeg() -> None:
    buf = io.BytesIO()
    Image.new("RGBA", (300, 300), (10, 20, 30, 0)).save(buf, format="PNG")

    out, mime = resize_avatar(buf.getvalue())

    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(out)) as result:
        assert result.mode == "RGB"  # no alpha channel survives
