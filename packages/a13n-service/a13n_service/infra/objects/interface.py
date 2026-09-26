"""The object-store contract: owner-named keys, create-only writes, verified reads, listing and deletion.

Every object is immutable. There is no conditional replacement: an owner makes new bytes reachable by
committing a reference to a new key in PostgreSQL, and only the owner deletes what it no longer references.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Protocol

from a13n_service.infra.errors import ServiceError, conflict

MAX_KEY_LENGTH = 1024
_KEY = re.compile(r"^[a-z0-9][a-z0-9_.-]*(/[a-z0-9][a-z0-9_.-]*)*$")


@dataclass(frozen=True, slots=True)
class ObjectRef:
    key: str
    digest: str
    size: int
    content_type: str


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, *, content_type: str) -> ObjectRef:
        """Create `key` once. Identical bytes at an existing key succeed, so an uncertain write is resolved by
        repeating it; different bytes raise `conflict` and never replace what is stored."""
        ...

    async def get(self, key: str) -> bytes | None: ...

    async def keys(self, prefix: str, *, limit: int, after: str | None = None) -> list[str]:
        """At most `limit` keys below `prefix`, in lexical order, strictly after `after` when supplied."""
        ...

    async def delete(self, key: str) -> None:
        """Remove `key`; a missing key is not an error."""
        ...


def reference(key: str, data: bytes, content_type: str) -> ObjectRef:
    return ObjectRef(key=key, digest=hashlib.sha256(data).hexdigest(), size=len(data), content_type=content_type)


def validate_key(key: str) -> str:
    if len(key) > MAX_KEY_LENGTH or _KEY.fullmatch(key) is None or ".." in key:
        raise ValueError("Object keys are lowercase slash-separated segments")
    return key


async def read(store: ObjectStore, ref: ObjectRef) -> bytes:
    """A committed reference must name exactly the bytes it was committed with."""
    data = await store.get(ref.key)
    if data is None:
        raise ServiceError("unavailable", "Referenced object is missing", {"dependency": "objects"})
    if len(data) != ref.size or hashlib.sha256(data).hexdigest() != ref.digest:
        raise ServiceError("unavailable", "Referenced object does not match its digest", {"dependency": "objects"})
    return data


def refuse_different(key: str) -> ServiceError:
    return conflict("object", key, "different_bytes")
