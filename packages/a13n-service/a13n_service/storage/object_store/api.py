"""Provider-neutral object storage contract."""

from collections.abc import AsyncIterable, AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Protocol

MAX_KEY_BYTES = 1024
MAX_METADATA_BYTES = 2048
MAX_LIST_LIMIT = 1000
_METADATA_KEY_CHARACTERS = frozenset("!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")

ObjectSource = bytes | AsyncIterable[bytes]


@dataclass(frozen=True, slots=True)
class ByteRange:
    start: int
    end_exclusive: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.start, int) or isinstance(self.start, bool) or self.start < 0:
            raise InvalidObjectRequest("byte range start must be non-negative")
        if self.end_exclusive is not None:
            if not isinstance(self.end_exclusive, int) or isinstance(self.end_exclusive, bool):
                raise InvalidObjectRequest("byte range end must be an integer")
            if self.end_exclusive <= self.start:
                raise InvalidObjectRequest("byte range end must be greater than start")


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    key: str
    size: int
    content_type: str | None
    metadata: Mapping[str, str]
    modified_at: datetime
    version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class ObjectSummary:
    key: str
    size: int
    modified_at: datetime
    version: str


@dataclass(frozen=True, slots=True)
class ObjectPage:
    items: tuple[ObjectSummary, ...]
    cursor: str | None


class ObjectReader(Protocol):
    info: ObjectInfo

    def __aiter__(self) -> AsyncIterator[bytes]: ...


class ObjectStore(Protocol):
    async def put(
        self,
        key: str,
        source: ObjectSource,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
        if_none_match: bool = False,
        if_match: str | None = None,
    ) -> ObjectInfo: ...

    def open(self, key: str, *, byte_range: ByteRange | None = None) -> AbstractAsyncContextManager[ObjectReader]: ...

    async def stat(self, key: str) -> ObjectInfo: ...

    async def delete(self, key: str, *, if_match: str | None = None) -> None: ...

    async def list(self, *, prefix: str = "", cursor: str | None = None, limit: int = 100) -> ObjectPage: ...


class ObjectStoreError(Exception):
    """Base class for provider-neutral object failures."""


class ObjectNotFound(ObjectStoreError):
    """The requested key does not exist."""


class ObjectConflict(ObjectStoreError):
    """A conditional mutation did not match current state."""


class ObjectAccessDenied(ObjectStoreError):
    """The backend rejected access; retrying the same request cannot grant it."""


class InvalidObjectRequest(ObjectStoreError, ValueError):
    """The request cannot be represented by the object contract."""


class ObjectStoreUnavailable(ObjectStoreError):
    """The backend is unavailable or returned invalid state."""


def validate_key(key: str) -> str:
    if not key or "\x00" in key:
        raise InvalidObjectRequest("object key must be non-empty and contain no NUL")
    try:
        encoded = key.encode("utf-8")
    except UnicodeEncodeError as error:
        raise InvalidObjectRequest("object key must be valid UTF-8") from error
    if len(encoded) > MAX_KEY_BYTES:
        raise InvalidObjectRequest(f"object key exceeds {MAX_KEY_BYTES} UTF-8 bytes")
    windows_path = PureWindowsPath(key)
    path = PurePosixPath(key.replace("\\", "/"))
    if windows_path.is_absolute() or windows_path.drive or path.is_absolute() or ".." in path.parts:
        raise InvalidObjectRequest("object key must not be absolute or contain parent traversal")
    return key


def normalize_metadata(metadata: Mapping[str, str] | None) -> dict[str, str]:
    values = dict(metadata or {})
    normalized: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise InvalidObjectRequest("object metadata keys and values must be strings")
        try:
            key.encode("ascii")
            value.encode("ascii")
        except UnicodeEncodeError as error:
            raise InvalidObjectRequest("object metadata must contain only ASCII characters") from error
        if any(character not in _METADATA_KEY_CHARACTERS for character in key):
            raise InvalidObjectRequest("object metadata keys must be valid HTTP field names")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise InvalidObjectRequest("object metadata values must not contain control characters")
        normalized_key = key.lower()
        if not normalized_key or normalized_key in normalized:
            raise InvalidObjectRequest("object metadata keys must be non-empty and unique ignoring case")
        normalized[normalized_key] = value
    encoded_size = sum(len(key) + len(value) for key, value in normalized.items())
    if encoded_size > MAX_METADATA_BYTES:
        raise InvalidObjectRequest(f"object metadata exceeds {MAX_METADATA_BYTES} UTF-8 bytes")
    return normalized


def validate_conditions(*, if_none_match: bool, if_match: str | None) -> None:
    if if_none_match and if_match is not None:
        raise InvalidObjectRequest("if_none_match and if_match are mutually exclusive")
    if if_match == "":
        raise InvalidObjectRequest("if_match must be non-empty")


def validate_list_request(prefix: str, cursor: str | None, limit: int) -> None:
    if prefix:
        validate_key(prefix)
    if not 1 <= limit <= MAX_LIST_LIMIT:
        raise InvalidObjectRequest(f"list limit must be between 1 and {MAX_LIST_LIMIT}")
    if cursor == "":
        raise InvalidObjectRequest("cursor must be non-empty")


async def iter_source(source: ObjectSource) -> AsyncIterator[bytes]:
    if isinstance(source, bytes):
        if source:
            yield source
        return
    async for chunk in source:
        if not isinstance(chunk, bytes) or not chunk:
            raise InvalidObjectRequest("object source must yield non-empty bytes")
        yield chunk


async def iter_parts(source: ObjectSource, part_size: int) -> AsyncIterator[bytes]:
    buffer = bytearray()
    async for chunk in iter_source(source):
        view = memoryview(chunk)
        while view:
            needed = part_size - len(buffer)
            buffer.extend(view[:needed])
            view = view[needed:]
            if len(buffer) == part_size:
                yield bytes(buffer)
                buffer.clear()
    if buffer:
        yield bytes(buffer)
