"""Explicit, bounded local image acquisition. Never called by text paste."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageGrab, UnidentifiedImageError

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_DRAFT_BYTES = 20 * 1024 * 1024
MAX_IMAGES = 8
_MEDIA = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}


@dataclass(frozen=True, slots=True)
class DraftImage:
    name: str
    data: bytes
    media_type: str


def image_bytes(name: str, data: bytes) -> DraftImage:
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
    return DraftImage(name, data, media_type)


def read_image(path: Path) -> DraftImage:
    if not path.is_file():
        raise ValueError("Attach a regular image file.")
    with path.open("rb") as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    return image_bytes(path.name, data)


def clipboard_images() -> tuple[DraftImage, ...]:
    try:
        value = ImageGrab.grabclipboard()
        if isinstance(value, Image.Image):
            if value.width * value.height > 32_000_000:
                raise ValueError("Clipboard image exceeds 32 megapixels.")
            stream = BytesIO()
            value.save(stream, format="PNG")
            return (image_bytes("clipboard.png", stream.getvalue()),)
        if isinstance(value, list):
            if len(value) > MAX_IMAGES:
                raise ValueError("Clipboard contains more than eight files.")
            return tuple(read_image(Path(path)) for path in value)
    except (OSError, NotImplementedError) as exc:
        raise ValueError(
            "Image clipboard unavailable. Use /attach <image-path>; Linux needs wl-paste or xclip."
        ) from exc
    raise ValueError("No image in the clipboard. Paste text normally, or use /attach <image-path>.")


def add_images(current: tuple[DraftImage, ...], incoming: tuple[DraftImage, ...]) -> tuple[DraftImage, ...]:
    result = current + incoming
    if len(result) > MAX_IMAGES or sum(len(image.data) for image in result) > MAX_DRAFT_BYTES:
        raise ValueError("A draft supports up to eight images and 20 MiB total. Use /remove first.")
    return result
