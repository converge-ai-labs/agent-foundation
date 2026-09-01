"""Bounded private staging for uploaded Plugin Wheels."""

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

from .errors import PluginError, plugin_artifact_invalid, plugin_artifact_limit, plugin_artifact_unavailable

_CHUNK_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class StagedPluginWheel:
    path: Path
    size_bytes: int
    content_digest: str
    _limiter: CapacityLimiter | None

    async def chunks(self) -> AsyncIterator[bytes]:
        try:
            async with await anyio.open_file(self.path, "rb", limiter=self._limiter) as source:
                while chunk := await source.read(_CHUNK_BYTES):
                    yield chunk
        except OSError as error:
            raise plugin_artifact_unavailable() from error

    async def remove(self) -> None:
        with anyio.move_on_after(5, shield=True):
            await to_thread.run_sync(_unlink_if_present, self.path, limiter=self._limiter)


class PluginStaging:
    def __init__(self, root: Path, *, limiter: CapacityLimiter | None = None) -> None:
        self._root = root
        self._limiter = limiter

    @classmethod
    async def create(cls, files_root: Path, *, limiter: CapacityLimiter | None = None) -> PluginStaging:
        root = await prepare_root(files_root / "plugin-staging-v1", create=True, limiter=limiter)
        return cls(root, limiter=limiter)

    async def stage(
        self,
        source: AsyncIterable[bytes],
        *,
        max_size_bytes: int,
        content_length: int | None,
    ) -> StagedPluginWheel:
        if content_length is not None and content_length > max_size_bytes:
            raise plugin_artifact_limit()
        path = self._root / f"{uuid.uuid4().hex}.whl.stage"
        digest = hashlib.sha256()
        size = 0
        try:
            target = await anyio.open_file(path, "xb", limiter=self._limiter)
            try:
                await to_thread.run_sync(os.chmod, path, 0o600, limiter=self._limiter)
                async for chunk in source:
                    if not isinstance(chunk, bytes):
                        raise plugin_artifact_invalid("invalid_body_chunk")
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_size_bytes:
                        raise plugin_artifact_limit()
                    digest.update(chunk)
                    await target.write(chunk)
                await target.flush()
            finally:
                with anyio.move_on_after(5, shield=True):
                    await target.aclose()
            if size == 0:
                raise plugin_artifact_invalid("empty_wheel")
            return StagedPluginWheel(
                path=path,
                size_bytes=size,
                content_digest=digest.hexdigest(),
                _limiter=self._limiter,
            )
        except PluginError:
            await _remove_failed(path, self._limiter)
            raise
        except (OSError, RuntimeError) as error:
            await _remove_failed(path, self._limiter)
            raise plugin_artifact_unavailable() from error


async def _remove_failed(path: Path, limiter: CapacityLimiter | None) -> None:
    with anyio.move_on_after(5, shield=True):
        await to_thread.run_sync(_unlink_if_present, path, limiter=limiter)


def _unlink_if_present(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
