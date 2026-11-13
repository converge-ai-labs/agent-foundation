"""Host-owned immutable objects available to the current Agent authority."""

from __future__ import annotations

import re
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

OBJECT_URI_PREFIX = "a13n-object://"
SUPPORTS_OBJECT_REFS = "a13n.harness.supports_object_refs"
OBJECT_STORE_CAPABILITY_ID = "a13n.object-store"
OBJECT_REF_MANAGEMENT = "a13n.harness.object-ref-management"
_OBJECT_REF = re.compile(r"a13n-object://obj_[a-zA-Z0-9_-]+")


class ObjectInfo(BaseModel):
    """Payload-free catalog entry. An ID never grants access or changes bytes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    object_id: str = Field(pattern=r"^obj_[a-zA-Z0-9_-]+$")
    media_type: str
    size: int = Field(ge=0)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str | None = None
    retention: Literal["temporary", "stored"] = "temporary"

    @property
    def ref(self) -> str:
        return OBJECT_URI_PREFIX + self.object_id

    def presentation(self) -> dict[str, object]:
        return {"ref": self.ref, **self.model_dump(mode="json")}


class ObjectStoreError(Exception):
    """Model-safe failure supplied by the Host Store."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@runtime_checkable
class ObjectStore(Protocol):
    """A scoped Host catalog with immutable bytes and two retention tiers.

    Temporary entries form a bounded LRU. Promotion is idempotent and preserves
    identity and bytes. A completed read owns its bytes independently of later
    eviction/deletion. There are no pins or approval-lifetime leases.
    """

    async def put(self, data: bytes, *, media_type: str, name: str | None = None) -> ObjectInfo: ...

    async def read(self, object_id: str) -> bytes: ...

    async def info(self, object_id: str) -> ObjectInfo: ...

    async def store(self, object_id: str) -> ObjectInfo: ...

    async def list(self, *, offset: int = 0, limit: int = 50) -> list[ObjectInfo]: ...

    async def delete(self, object_id: str) -> None: ...


def object_id(ref: str) -> str:
    """Parse only the reserved complete URI, never paths or network locations."""
    if not is_object_ref(ref):
        raise ObjectStoreError("object_ref_invalid", "Use an exact a13n-object://obj_ reference returned by the Store.")
    return ref.removeprefix(OBJECT_URI_PREFIX)


def is_object_ref(value: str) -> bool:
    return _OBJECT_REF.fullmatch(value) is not None


def contains_object_ref(value: object) -> bool:
    if isinstance(value, BaseModel):
        value = dict(value)
    if isinstance(value, str):
        return is_object_ref(value)
    if isinstance(value, dict):
        return any(contains_object_ref(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(contains_object_ref(item) for item in value)
    return False


def object_not_found() -> ObjectStoreError:
    return ObjectStoreError(
        "object_ref_not_found",
        "Object is unavailable, expired, deleted, or LRU-evicted. Use a listed ref or explicitly import again; "
        "store objects before long-term use. Do not guess IDs or automatically replay the producer.",
    )
