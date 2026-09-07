"""Explicit, bounded local attachment acquisition. Never called by text paste."""

from __future__ import annotations

import mimetypes
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageGrab

from a13n_harness_ui.input_images import MAX_IMAGE_BYTES, image_bytes
from a13n_harness_ui.thread_files import MAX_ATTACHMENTS, MAX_INPUT_BYTES, AttachmentUpload


def read_image(path: Path) -> AttachmentUpload:
    if not path.is_file():
        raise ValueError("Attach a regular image file.")
    with path.open("rb") as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    return image_bytes(path.name, data)


def read_attachment(path: Path) -> AttachmentUpload:
    if not path.is_file():
        raise ValueError("Attach a regular file.")
    with path.open("rb") as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Attachment exceeds 10 MiB.")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if media_type.startswith("image/"):
        return image_bytes(path.name, data)
    return AttachmentUpload(path.name, data, media_type)


def clipboard_images() -> tuple[AttachmentUpload, ...]:
    try:
        value = ImageGrab.grabclipboard()
        if isinstance(value, Image.Image):
            if value.width * value.height > 32_000_000:
                raise ValueError("Clipboard image exceeds 32 megapixels.")
            stream = BytesIO()
            value.save(stream, format="PNG")
            return (image_bytes("clipboard.png", stream.getvalue()),)
        if isinstance(value, list):
            if len(value) > MAX_ATTACHMENTS:
                raise ValueError("Clipboard contains more than eight files.")
            return tuple(read_attachment(Path(path)) for path in value)
    except (OSError, NotImplementedError) as exc:
        raise ValueError(
            "Image clipboard unavailable. Use /attach <image-path>; Linux needs wl-paste or xclip."
        ) from exc
    raise ValueError("No image in the clipboard. Paste text normally, or use /attach <image-path>.")


def add_images(
    current: tuple[AttachmentUpload, ...], incoming: tuple[AttachmentUpload, ...]
) -> tuple[AttachmentUpload, ...]:
    result = current + incoming
    if len(result) > MAX_ATTACHMENTS or sum(len(image.data) for image in result) > MAX_INPUT_BYTES:
        raise ValueError("A draft supports up to eight attachments and 20 MiB total. Use /remove first.")
    return result
