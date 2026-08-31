"""Bounded private staging for Asset upload and verified delivery."""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import anyio
from anyio import CapacityLimiter, to_thread

from a13n_service.storage.filesystem import prepare_root

from .errors import AssetManagementError, asset_content_invalid, asset_content_unavailable, asset_limit

_CHUNK_BYTES = 256 * 1024
_SNIFF_BYTES = 32


@dataclass(frozen=True, slots=True)
class StagedAssetContent:
    path: Path
    size_bytes: int
    content_sha256: str
    detected_media_type: str | None
    _limiter: CapacityLimiter | None

    async def chunks(self) -> AsyncIterator[bytes]:
        try:
            async with await anyio.open_file(self.path, "rb", limiter=self._limiter) as source:
                while chunk := await source.read(_CHUNK_BYTES):
                    yield chunk
        except OSError as error:
            raise asset_content_unavailable() from error

    async def remove(self) -> None:
        with anyio.move_on_after(5, shield=True):
            await to_thread.run_sync(_unlink_if_present, self.path, limiter=self._limiter)


class AssetStaging:
    def __init__(self, root: Path, *, limiter: CapacityLimiter | None = None) -> None:
        self._root = root
        self._limiter = limiter

    @classmethod
    async def create(cls, files_root: Path, *, limiter: CapacityLimiter | None = None) -> AssetStaging:
        root = await prepare_root(files_root / "asset-staging-v1", create=True, limiter=limiter)
        return cls(root, limiter=limiter)

    async def stage_upload(
        self,
        source: AsyncIterable[bytes],
        *,
        max_size_bytes: int,
        content_length: int | None,
    ) -> StagedAssetContent:
        if content_length is not None and content_length > max_size_bytes:
            raise asset_limit()
        return await self._stage(
            source,
            max_size_bytes=max_size_bytes,
            expected_size=None,
            expected_digest=None,
            inspect_media_type=True,
        )

    async def stage_verified(
        self,
        source: AsyncIterable[bytes],
        *,
        expected_size: int,
        expected_digest: str,
    ) -> StagedAssetContent:
        return await self._stage(
            source,
            max_size_bytes=expected_size,
            expected_size=expected_size,
            expected_digest=expected_digest,
            inspect_media_type=False,
        )

    async def _stage(
        self,
        source: AsyncIterable[bytes],
        *,
        max_size_bytes: int,
        expected_size: int | None,
        expected_digest: str | None,
        inspect_media_type: bool,
    ) -> StagedAssetContent:
        path = self._root / f"{uuid.uuid4().hex}.stage"
        digest = hashlib.sha256()
        size = 0
        prefix = bytearray()
        try:
            target = await anyio.open_file(path, "xb", limiter=self._limiter)
            try:
                await to_thread.run_sync(os.chmod, path, 0o600, limiter=self._limiter)
                async for chunk in source:
                    if not isinstance(chunk, bytes):
                        raise asset_content_invalid()
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_size_bytes:
                        raise asset_limit() if expected_size is None else asset_content_unavailable()
                    digest.update(chunk)
                    if inspect_media_type and len(prefix) < _SNIFF_BYTES:
                        prefix.extend(chunk[: _SNIFF_BYTES - len(prefix)])
                    await target.write(chunk)
                await target.flush()
            finally:
                with anyio.move_on_after(5, shield=True):
                    await target.aclose()
            content_digest = digest.hexdigest()
            if expected_size is not None and size != expected_size:
                raise asset_content_unavailable()
            if expected_digest is not None and content_digest != expected_digest:
                raise asset_content_unavailable()
            return StagedAssetContent(
                path=path,
                size_bytes=size,
                content_sha256=content_digest,
                detected_media_type=_detect_media_type(bytes(prefix)) if inspect_media_type else None,
                _limiter=self._limiter,
            )
        except AssetManagementError:
            await _remove_failed(path, self._limiter)
            raise
        except (OSError, RuntimeError) as error:
            await _remove_failed(path, self._limiter)
            raise asset_content_unavailable() from error


def _detect_media_type(prefix: bytes) -> str | None:
    if prefix.startswith(b"%PDF-"):
        return "application/pdf"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(prefix) >= 12 and prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        return "image/webp"
    if prefix.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "application/zip"
    return None


async def _remove_failed(path: Path, limiter: CapacityLimiter | None) -> None:
    with anyio.move_on_after(5, shield=True):
        await to_thread.run_sync(_unlink_if_present, path, limiter=limiter)


def _unlink_if_present(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
