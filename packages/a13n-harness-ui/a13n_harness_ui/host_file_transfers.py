"""Revision-scoped browser file access and bounded HTTP range delivery."""

from __future__ import annotations

import base64
import binascii
import hmac
import re
import secrets
import time
from collections.abc import AsyncIterable, Awaitable, Callable
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi.responses import Response, StreamingResponse
from pydantic import ValidationError
from starlette.requests import HTTPConnection
from starlette.types import Receive, Scope, Send

from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.host_files import FILE_CHUNK_BYTES, FileStream, NativePath, Revision, file_media_type
from a13n_harness_ui.surfaces import SurfaceModel

TRANSFER_PATH = "/api/host/files/transfer"
TRANSFER_TTL_SECONDS = 30 * 60


class FileTransferRequest(SurfaceModel):
    path: NativePath
    expected_revision: Revision
    disposition: Literal["attachment", "inline"] = "attachment"


class FileTransferAccess(SurfaceModel):
    url: str
    expires_at: int


class _Claims(FileTransferRequest):
    expires_at: int
    media_type: str


class FileTransfers:
    """Stateless capabilities valid for only one listener lifetime and file revision."""

    def __init__(self) -> None:
        self._secret = secrets.token_bytes(32)

    def issue(self, request: FileTransferRequest, resolved_path: Path) -> FileTransferAccess:
        media_type = file_media_type(resolved_path)
        # Response safety is independent of which renderer the frontend chooses.
        # Unknown and active document types always remain detached downloads.
        passive = media_type.startswith(("audio/", "video/")) or (
            media_type.startswith("image/") and media_type != "image/svg+xml"
        )
        inline = request.disposition == "inline" and passive
        claims = _Claims(
            **{
                **request.model_dump(),
                "disposition": "inline" if inline else "attachment",
            },
            expires_at=int(time.time()) + TRANSFER_TTL_SECONDS,
            media_type=media_type if inline else "application/octet-stream",
        )
        payload = base64.urlsafe_b64encode(claims.model_dump_json().encode()).decode()
        signature = hmac.digest(self._secret, payload.encode(), "sha256").hex()
        return FileTransferAccess(url=f"{TRANSFER_PATH}?token={payload}.{signature}", expires_at=claims.expires_at)

    def verify(self, token: str) -> _Claims:
        try:
            if len(token) > 32768:
                raise ValueError("Oversize capability")
            payload, signature = token.rsplit(".", 1)
            expected = hmac.digest(self._secret, payload.encode(), "sha256").hex()
            if not hmac.compare_digest(signature.encode(), expected.encode()):
                raise ValueError("Invalid signature")
            claims = _Claims.model_validate_json(base64.b64decode(payload, altchars=b"-_", validate=True))
            if claims.expires_at <= time.time():
                raise ValueError("Expired capability")
            return claims
        except (ValueError, ValidationError, binascii.Error):
            raise HarnessUiError(
                "File access expired or is invalid. Refresh to obtain a new file link.",
                code="host_files_transfer_expired",
            ) from None

    def admits(self, request: HTTPConnection) -> bool:
        if request.scope["type"] != "http" or request.scope["method"] not in {"GET", "HEAD"}:
            return False
        if request.scope["path"] != TRANSFER_PATH:
            return False
        try:
            self.verify(request.query_params.get("token", ""))
            return True
        except HarnessUiError:
            return False


def byte_range(value: str | None, size: int) -> tuple[int, int] | None:
    """Single byte ranges; ignore unknown units and multipart requests (RFC 9110)."""
    if value is None or not value.startswith("bytes=") or "," in value:
        return None
    match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", value)
    if match is None or size == 0:
        raise ValueError("Unsatisfiable byte range")
    first, last = match.groups()
    if not first:
        suffix = int(last or "0")
        if suffix == 0:
            raise ValueError("Empty suffix range")
        return max(0, size - suffix), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start > end:
        raise ValueError("Unsatisfiable byte range")
    return start, end


class FileStreamResponse(StreamingResponse):
    """Close the handle on completion, disconnect, or failure, even before iteration."""

    def __init__(
        self,
        opened: FileStream,
        content: AsyncIterable[bytes],
        *,
        status_code: int,
        headers: dict[str, str],
        media_type: str,
    ) -> None:
        self.opened = opened
        super().__init__(content, status_code=status_code, headers=headers, media_type=media_type)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.opened.close()


async def stream_response(
    request: HTTPConnection,
    opened: FileStream,
    read: Callable[[FileStream, int, int], Awaitable[bytes]],
    *,
    filename: str,
    media_type: str = "application/octet-stream",
    inline: bool = False,
) -> Response:
    """The returned response owns the handle; failures before return close it here."""
    try:
        # A bounded probe distinguishes empty disk files from native regular
        # files (for example procfs/sysfs) whose stat size is not their content length.
        first = await read(opened, 0, FILE_CHUNK_BYTES)
        size = opened.entry.size if len(first) == min(FILE_CHUNK_BYTES, opened.entry.size) else None
        etag = f'"{opened.entry.revision}"'
        headers = {
            "Accept-Ranges": "bytes" if size is not None else "none",
            "ETag": etag,
            "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(filename, safe='')}",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        }
        start, end = 0, size - 1 if size is not None else None
        status = 200
        if request.scope["method"] == "GET" and size is not None:
            requested = request.headers.get("range")
            if request.headers.get("if-range", etag) != etag:
                requested = None
            try:
                selected = byte_range(requested, size)
            except ValueError:
                await opened.close()
                return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
            if selected is not None:
                start, end = selected
                status = 206
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"
                first = await read(opened, start, min(FILE_CHUNK_BYTES, end - start + 1))
                if len(first) != min(FILE_CHUNK_BYTES, end - start + 1):
                    raise HarnessUiError(
                        "File changed during transfer; refresh before retrying.", code="host_files_conflict"
                    )
        if end is not None:
            headers["Content-Length"] = str(max(0, end - start + 1))
        if request.scope["method"] == "HEAD":
            await opened.close()
            response = Response(status_code=200, headers=headers, media_type=media_type)
            if size is None:
                del response.headers["Content-Length"]
            return response

        async def chunks() -> AsyncIterable[bytes]:
            offset = start
            if first:
                yield first
                offset += len(first)
            while end is None or offset <= end:
                count = FILE_CHUNK_BYTES if end is None else min(FILE_CHUNK_BYTES, end - offset + 1)
                chunk = await read(opened, offset, count)
                if end is not None and len(chunk) != count:
                    raise HarnessUiError(
                        "File changed during transfer; refresh before retrying.", code="host_files_conflict"
                    )
                if not chunk:
                    break
                yield chunk
                offset += len(chunk)

        return FileStreamResponse(opened, chunks(), status_code=status, headers=headers, media_type=media_type)
    except BaseException:
        await opened.close()
        raise
