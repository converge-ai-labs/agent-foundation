"""Explicit, bounded local attachment acquisition. Never called by text paste."""

from __future__ import annotations

import mimetypes
import os
import shutil
import sys
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


def _clipboard_failure(*, unavailable: bool) -> str:
    reason = "Image clipboard unavailable." if unavailable else "No image or files in the clipboard."
    if os.environ.get("SSH_TTY") or os.environ.get("SSH_CONNECTION"):
        return (
            f"{reason} In this SSH session, image paste reads the remote host's clipboard, "
            "not your local computer's. Text paste still works through your terminal "
            "(Cmd+V on macOS). Upload the file to the remote host, then use /attach <remote-path>. "
            "Installing wl-paste or xclip on the remote host does not forward your local clipboard."
        )
    if unavailable and sys.platform == "linux":
        if not os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
            reason += " No Wayland or X11 display session was detected; a clipboard helper alone is not enough."
        else:
            helper = "wl-paste" if os.environ.get("WAYLAND_DISPLAY") else "xclip"
            if shutil.which(helper) is None:
                reason += f" This display session needs {helper}, which was not found on PATH."
            else:
                reason += " The display clipboard could not be read; check that the display session is accessible."
    return f"{reason} Text paste still works through your terminal. Use /attach <path> for files on this host."


def clipboard_images() -> tuple[AttachmentUpload, ...]:
    # Environment hints explain failures, never preempt a working clipboard
    # (including a forwarded display). Normal text paste does not call this.
    try:
        value = ImageGrab.grabclipboard()
    except (OSError, NotImplementedError) as exc:
        raise ValueError(_clipboard_failure(unavailable=True)) from exc
    try:
        if isinstance(value, Image.Image):
            if value.width * value.height > 32_000_000:
                raise ValueError("Clipboard image exceeds 32 megapixels.")
            stream = BytesIO()
            value.save(stream, format="PNG")
            return (image_bytes("clipboard.png", stream.getvalue()),)
        if isinstance(value, list) and value:
            if len(value) > MAX_ATTACHMENTS:
                raise ValueError("Clipboard contains more than eight files.")
            return tuple(read_attachment(Path(path)) for path in value)
    except OSError as exc:
        raise ValueError(
            "Clipboard content could not be read or encoded. Check the copied image or file and retry."
        ) from exc
    raise ValueError(_clipboard_failure(unavailable=False))


def add_images(
    current: tuple[AttachmentUpload, ...], incoming: tuple[AttachmentUpload, ...]
) -> tuple[AttachmentUpload, ...]:
    result = current + incoming
    if len(result) > MAX_ATTACHMENTS or sum(len(image.data) for image in result) > MAX_INPUT_BYTES:
        raise ValueError("A draft supports up to eight attachments and 20 MiB total. Use /remove first.")
    return result
