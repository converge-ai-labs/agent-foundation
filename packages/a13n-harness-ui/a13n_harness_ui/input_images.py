"""Shared bounded image validation for every Harness UI input surface."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, UnidentifiedImageError

from a13n_harness_ui.thread_files import MAX_ATTACHMENT_BYTES, AttachmentUpload

MAX_IMAGE_BYTES = MAX_ATTACHMENT_BYTES
_MEDIA = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}


def image_bytes(name: str, data: bytes) -> AttachmentUpload:
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds 10 MiB. Resize it before attaching.")
    try:
        with Image.open(BytesIO(data)) as image:
            media_type = _MEDIA.get(image.format or "")
            if media_type is None:
                raise ValueError("Choose a PNG, JPEG, WebP, or GIF image.")
            if image.width * image.height > 32_000_000:
                raise ValueError("Image exceeds 32 megapixels. Resize it before attaching.")
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("This file is not a valid supported image.") from exc
    return AttachmentUpload(name, data, media_type)
