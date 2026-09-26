"""Filesystem object store for development and single-host deployments."""

import heapq
import os
from functools import partial
from pathlib import Path
from uuid import uuid4

from anyio import fail_after
from anyio.to_thread import run_sync

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.objects.interface import ObjectRef, reference, refuse_different, validate_key


class LocalObjects:
    def __init__(self, root: Path, *, max_bytes: int, timeout: float):
        if max_bytes < 1 or timeout <= 0:
            raise ValueError("Object bounds must be positive")
        self.root = root.resolve()
        self.max_bytes = max_bytes
        self.timeout = timeout

    def _path(self, key: str) -> Path:
        return self.root / validate_key(key)

    def _put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            try:
                # link() fails when the key exists, which makes creation atomic and never replaces bytes.
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != data:
                    raise refuse_different(key) from None
            else:
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def _get(self, key: str) -> bytes | None:
        try:
            with self._path(key).open("rb") as file:
                data = file.read(self.max_bytes + 1)
        except FileNotFoundError:
            return None
        if len(data) > self.max_bytes:
            raise ServiceError("unavailable", "Stored object exceeds its byte limit", {"dependency": "objects"})
        return data

    def _keys(self, prefix: str, limit: int, after: str | None) -> list[str]:
        base = self._path(prefix)
        if not base.is_dir():
            return []
        # Keep only one page in memory, in the same lexicographic order as S3 StartAfter.
        keys = (
            str(Path(directory, name).relative_to(self.root))
            for directory, _, files in os.walk(base)
            for name in files
            if not name.startswith(".")
        )
        return heapq.nsmallest(limit, (key for key in keys if after is None or key > after))

    def _delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    async def put(self, key: str, data: bytes, *, content_type: str) -> ObjectRef:
        if len(data) > self.max_bytes:
            raise ServiceError("payload_too_large", "Object exceeds its byte limit", {"limit": self.max_bytes})
        with fail_after(self.timeout):
            await run_sync(partial(self._put, key, data))
        return reference(key, data, content_type)

    async def get(self, key: str) -> bytes | None:
        with fail_after(self.timeout):
            return await run_sync(partial(self._get, key))

    async def keys(self, prefix: str, *, limit: int, after: str | None = None) -> list[str]:
        with fail_after(self.timeout):
            return await run_sync(partial(self._keys, prefix, limit, after))

    async def delete(self, key: str) -> None:
        with fail_after(self.timeout):
            await run_sync(partial(self._delete, key))
