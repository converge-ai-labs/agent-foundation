"""Single-process local object store with atomic envelope publication."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import struct
import uuid
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import anyio
from anyio import AsyncFile, CapacityLimiter, to_thread

from a13n_service.temporal import utc_now

from ..filesystem import prepare_root
from .api import (
    ByteRange,
    InvalidObjectRequest,
    ObjectAccessDenied,
    ObjectConflict,
    ObjectInfo,
    ObjectNotFound,
    ObjectPage,
    ObjectReader,
    ObjectSource,
    ObjectStoreUnavailable,
    ObjectSummary,
    iter_source,
    normalize_metadata,
    validate_conditions,
    validate_key,
    validate_list_request,
)

_MAGIC = b"CVOBJ001"
_HEADER_CAPACITY = 64 * 1024
_PREFIX_SIZE = len(_MAGIC) + 4
_BODY_OFFSET = _PREFIX_SIZE + _HEADER_CAPACITY


class LocalObjectStore:
    def __init__(self, root: Path, *, chunk_size: int = 256 * 1024, limiter: CapacityLimiter | None = None) -> None:
        self._root = root
        self._objects = root / "objects-v1"
        self._temporary = root / "tmp"
        self._chunk_size = chunk_size
        self._limiter = limiter
        self._write_lock = anyio.Lock()

    @classmethod
    async def create(
        cls,
        root: Path,
        *,
        create_root: bool = True,
        chunk_size: int = 256 * 1024,
        limiter: CapacityLimiter | None = None,
    ) -> LocalObjectStore:
        resolved = await prepare_root(root, create=create_root, limiter=limiter)
        store = cls(resolved, chunk_size=chunk_size, limiter=limiter)
        await to_thread.run_sync(store._prepare_layout, limiter=limiter)
        return store

    async def put(
        self,
        key: str,
        source: ObjectSource,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
        if_none_match: bool = False,
        if_match: str | None = None,
    ) -> ObjectInfo:
        validate_key(key)
        validate_conditions(if_none_match=if_none_match, if_match=if_match)
        normalized_metadata = normalize_metadata(metadata)
        temporary = self._temporary / f"{uuid.uuid4().hex}.upload"
        destination = self._object_path(key)
        await to_thread.run_sync(self._prepare_shard, destination.parent, limiter=self._limiter)

        try:
            info = await self._write_envelope(temporary, key, source, content_type, normalized_metadata)
            async with self._write_lock:
                current = await self._stat_if_present(destination, key)
                self._check_write_condition(current, if_none_match=if_none_match, if_match=if_match)
                await to_thread.run_sync(os.replace, temporary, destination, limiter=self._limiter)
            return info
        finally:
            with anyio.move_on_after(5, shield=True):
                await to_thread.run_sync(_unlink_if_present, temporary, limiter=self._limiter)

    @asynccontextmanager
    async def open(self, key: str, *, byte_range: ByteRange | None = None) -> AsyncGenerator[ObjectReader]:
        validate_key(key)
        path = self._object_path(key)
        shard_exists = await to_thread.run_sync(
            _validate_private_directory,
            self._objects,
            path.parent,
            limiter=self._limiter,
        )
        if not shard_exists:
            raise ObjectNotFound(key)
        try:
            file = await anyio.open_file(path, "rb", opener=_open_no_follow, limiter=self._limiter)
        except FileNotFoundError as error:
            raise ObjectNotFound(key) from error
        except PermissionError as error:
            raise ObjectAccessDenied("local object access denied") from error
        except OSError as error:
            raise ObjectStoreUnavailable("local object could not be opened") from error
        try:
            info = await _read_header_from_file(file, expected_key=key)
            start, length = _select_range(info.size, byte_range)
            await file.seek(_BODY_OFFSET + start)
            yield _LocalReader(file, info, remaining=length, chunk_size=self._chunk_size)
        finally:
            await file.aclose()

    async def stat(self, key: str) -> ObjectInfo:
        validate_key(key)
        info = await self._stat_if_present(self._object_path(key), key)
        if info is None:
            raise ObjectNotFound(key)
        return info

    async def delete(self, key: str, *, if_match: str | None = None) -> None:
        validate_key(key)
        if if_match == "":
            raise InvalidObjectRequest("if_match must be non-empty")
        path = self._object_path(key)
        async with self._write_lock:
            current = await self._stat_if_present(path, key)
            if current is None:
                if if_match is not None:
                    raise ObjectConflict(key)
                return
            if if_match is not None and current.version != if_match:
                raise ObjectConflict(key)
            await to_thread.run_sync(path.unlink, limiter=self._limiter)

    async def list(self, *, prefix: str = "", cursor: str | None = None, limit: int = 100) -> ObjectPage:
        validate_list_request(prefix, cursor, limit)
        after = _decode_cursor(cursor, prefix) if cursor is not None else None
        infos = await to_thread.run_sync(self._scan_headers, limiter=self._limiter)
        summaries = sorted(
            (
                ObjectSummary(info.key, info.size, info.modified_at, info.version)
                for info in infos
                if info.key.startswith(prefix) and (after is None or info.key.encode() > after.encode())
            ),
            key=lambda item: item.key.encode(),
        )
        page = summaries[:limit]
        next_cursor = _encode_cursor(prefix, page[-1].key) if len(summaries) > limit else None
        return ObjectPage(tuple(page), next_cursor)

    def _prepare_layout(self) -> None:
        _prepare_private_directory(self._root, self._objects)
        _prepare_private_directory(self._root, self._temporary)

    def _prepare_shard(self, shard: Path) -> None:
        _prepare_private_directory(self._objects, shard)

    def _object_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode()).hexdigest()
        return self._objects / digest[:2] / f"{digest}.object"

    async def _write_envelope(
        self,
        path: Path,
        key: str,
        source: ObjectSource,
        content_type: str | None,
        metadata: dict[str, str],
    ) -> ObjectInfo:
        size = 0
        modified_at = utc_now()
        file = await anyio.open_file(path, "xb", limiter=self._limiter)
        try:
            await to_thread.run_sync(lambda: os.chmod(path, 0o600), limiter=self._limiter)
            await file.write(b"\x00" * _BODY_OFFSET)
            async for chunk in iter_source(source):
                size += len(chunk)
                await file.write(chunk)
            version = uuid.uuid4().hex
            header = _encode_header(key, size, content_type, metadata, modified_at, version)
            await file.seek(0)
            await file.write(_MAGIC + struct.pack(">I", len(header)) + header)
            await file.flush()
        finally:
            with anyio.move_on_after(5, shield=True):
                await file.aclose()
        return ObjectInfo(key, size, content_type, metadata, modified_at, version)

    async def _stat_if_present(self, path: Path, key: str) -> ObjectInfo | None:
        shard_exists = await to_thread.run_sync(
            _validate_private_directory,
            self._objects,
            path.parent,
            limiter=self._limiter,
        )
        if not shard_exists:
            return None
        try:
            return await to_thread.run_sync(_read_header, path, key, limiter=self._limiter)
        except FileNotFoundError:
            return None

    @staticmethod
    def _check_write_condition(current: ObjectInfo | None, *, if_none_match: bool, if_match: str | None) -> None:
        if if_none_match and current is not None:
            raise ObjectConflict("object already exists")
        if if_match is not None and (current is None or current.version != if_match):
            raise ObjectConflict("object version does not match")

    def _scan_headers(self) -> list[ObjectInfo]:
        infos: list[ObjectInfo] = []
        for shard in self._objects.iterdir():
            if not _validate_private_directory(self._objects, shard):
                continue
            for path in shard.glob("*.object"):
                infos.append(_read_header(path))
        return infos


class _LocalReader:
    def __init__(self, file: AsyncFile[bytes], info: ObjectInfo, *, remaining: int, chunk_size: int) -> None:
        self._file = file
        self.info = info
        self._remaining = remaining
        self._chunk_size = chunk_size

    def __aiter__(self) -> _LocalReader:
        return self

    async def __anext__(self) -> bytes:
        if self._remaining == 0:
            raise StopAsyncIteration
        chunk = await self._file.read(min(self._chunk_size, self._remaining))
        if not chunk:
            raise ObjectStoreUnavailable("local object body is truncated")
        self._remaining -= len(chunk)
        return chunk


def _encode_header(
    key: str,
    size: int,
    content_type: str | None,
    metadata: Mapping[str, str],
    modified_at: datetime,
    version: str,
) -> bytes:
    encoded = json.dumps(
        {
            "content_type": content_type,
            "key": key,
            "metadata": dict(metadata),
            "modified_at": modified_at.isoformat(),
            "size": size,
            "version": version,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    if len(encoded) > _HEADER_CAPACITY:
        raise InvalidObjectRequest("object header exceeds local storage capacity")
    return encoded


def _read_header(path: Path, expected_key: str | None = None) -> ObjectInfo:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise
    except PermissionError as error:
        raise ObjectAccessDenied("local object access denied") from error
    except OSError as error:
        raise ObjectStoreUnavailable("local object could not be opened") from error
    with os.fdopen(descriptor, "rb") as file:
        prefix = file.read(_PREFIX_SIZE)
        if len(prefix) != _PREFIX_SIZE or prefix[: len(_MAGIC)] != _MAGIC:
            raise ObjectStoreUnavailable("local object has an invalid envelope")
        length = struct.unpack(">I", prefix[len(_MAGIC) :])[0]
        if length > _HEADER_CAPACITY:
            raise ObjectStoreUnavailable("local object has an invalid header length")
        header = file.read(length)
        info = _decode_header(header, expected_key)
        file.seek(0, os.SEEK_END)
        if file.tell() != _BODY_OFFSET + info.size:
            raise ObjectStoreUnavailable("local object body size does not match its metadata")
    return info


async def _read_header_from_file(file: AsyncFile[bytes], *, expected_key: str) -> ObjectInfo:
    prefix = await file.read(_PREFIX_SIZE)
    if len(prefix) != _PREFIX_SIZE or prefix[: len(_MAGIC)] != _MAGIC:
        raise ObjectStoreUnavailable("local object has an invalid envelope")
    length = struct.unpack(">I", prefix[len(_MAGIC) :])[0]
    if length > _HEADER_CAPACITY:
        raise ObjectStoreUnavailable("local object has an invalid header length")
    info = _decode_header(await file.read(length), expected_key)
    await file.seek(0, os.SEEK_END)
    if await file.tell() != _BODY_OFFSET + info.size:
        raise ObjectStoreUnavailable("local object body size does not match its metadata")
    return info


def _decode_header(header: bytes, expected_key: str | None) -> ObjectInfo:
    try:
        value = json.loads(header)
        key = value["key"]
        size = value["size"]
        content_type = value["content_type"]
        metadata = value["metadata"]
        modified_at = datetime.fromisoformat(value["modified_at"])
        version = value["version"]
        if not isinstance(key, str):
            raise ValueError
        validate_key(key)
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError
        if content_type is not None and not isinstance(content_type, str):
            raise ValueError
        normalized_metadata = normalize_metadata(metadata)
        if modified_at.utcoffset() is None:
            raise ValueError
        if not isinstance(version, str) or not version:
            raise ValueError
        info = ObjectInfo(
            key=key,
            size=size,
            content_type=content_type,
            metadata=normalized_metadata,
            modified_at=modified_at,
            version=version,
        )
    except (InvalidObjectRequest, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ObjectStoreUnavailable("local object has invalid metadata") from error
    if expected_key is not None and info.key != expected_key:
        raise ObjectStoreUnavailable("local object key digest collision")
    return info


def _select_range(size: int, byte_range: ByteRange | None) -> tuple[int, int]:
    if byte_range is None:
        return 0, size
    if byte_range.start >= size:
        raise InvalidObjectRequest("byte range starts beyond the object")
    end = min(byte_range.end_exclusive if byte_range.end_exclusive is not None else size, size)
    return byte_range.start, end - byte_range.start


def _encode_cursor(prefix: str, last_key: str) -> str:
    payload = json.dumps({"prefix": prefix, "last_key": last_key}, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str, prefix: str) -> str:
    try:
        padding = "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if value["prefix"] != prefix or not isinstance(value["last_key"], str):
            raise ValueError
        return value["last_key"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise InvalidObjectRequest("invalid object list cursor") from error


def _unlink_if_present(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _prepare_private_directory(parent: Path, directory: Path) -> None:
    if _validate_private_directory(parent, directory):
        return
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    if not _validate_private_directory(parent, directory):
        raise ObjectStoreUnavailable(f"local object layout directory disappeared: {directory.name}")


def _validate_private_directory(parent: Path, directory: Path) -> bool:
    try:
        mode = directory.lstat().st_mode
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise ObjectStoreUnavailable(f"local object layout path is not a directory: {directory.name}")
    if not directory.resolve(strict=True).is_relative_to(parent.resolve(strict=True)):
        raise ObjectStoreUnavailable("local object layout escapes its configured root")
    return True


def _open_no_follow(path: str, flags: int) -> int:
    return os.open(path, flags | os.O_NOFOLLOW)
