"""Image processing for user uploads.

Avatars are downscaled at upload time so only a small thumbnail reaches object
storage — a profile picture shown at ~40px never needs the multi-megabyte
original a phone camera produces. This protects MinIO storage and the bandwidth
spent every time the avatar is served.
"""

import io

from PIL import Image, ImageOps

# Retina-friendly ceiling: an avatar is displayed small, but we keep some room
# for larger profile views and high-DPI screens. Anything bigger is wasted.
AVATAR_MAX_DIMENSION = 512
AVATAR_JPEG_QUALITY = 85


def resize_avatar(data: bytes) -> tuple[bytes, str]:
    """Downscale an avatar image and re-encode it as JPEG.

    Returns (jpeg_bytes, "image/jpeg"). Honors EXIF orientation (phone photos are
    often stored sideways) and flattens transparency onto white so the JPEG has
    no black background. Never upscales — a small image is returned as-is (only
    re-encoded). The caller stores the returned bytes instead of the original.
    """
    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img)

        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            background = Image.new("RGBA", img.size, (255, 255, 255, 255))
            background.alpha_composite(img)
            img = background.convert("RGB")
        else:
            img = img.convert("RGB")

        # thumbnail() preserves aspect ratio and never enlarges a smaller image.
        img.thumbnail((AVATAR_MAX_DIMENSION, AVATAR_MAX_DIMENSION), Image.LANCZOS)

        out = io.BytesIO()
        img.save(out, format="JPEG", quality=AVATAR_JPEG_QUALITY, optimize=True)
        return out.getvalue(), "image/jpeg"
