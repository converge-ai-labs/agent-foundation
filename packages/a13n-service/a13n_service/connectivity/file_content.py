"""Bounded attachment values shared by platform file transports."""

import mimetypes
from dataclasses import dataclass

import httpx2
from a13n_harness.providers.http import ProviderHttpError, bounded_response_body

from a13n_service.assets.domain import normalize_asset_filename

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS = 5


@dataclass(frozen=True, repr=False)
class FileContent:
    filename: str
    media_type: str
    body: bytes


def safe_filename(value: object, fallback: str) -> str:
    try:
        return normalize_asset_filename(value) if isinstance(value, str) else fallback
    except ValueError:
        return fallback


def media_type(filename: str, hint: object = None) -> str:
    candidate = hint.split(";", 1)[0].strip().lower() if isinstance(hint, str) else ""
    if candidate in {"", "application/octet-stream", "binary/octet-stream"}:
        candidate = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    if candidate not in {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "application/pdf",
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/json",
    }:
        raise ProviderHttpError("unsupported_attachment_type")
    return candidate


async def read_file(response: httpx2.Response) -> bytes:
    if response.status_code != 200:
        raise ProviderHttpError("attachment_download_failed")
    body = await bounded_response_body(response, max_bytes=MAX_FILE_BYTES)
    if not body:
        raise ProviderHttpError("attachment_empty")
    return body
