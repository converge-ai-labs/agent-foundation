"""Bounded display values stored once per unchanged field; no chunking or content-based deduplication."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from a13n_harness import StoredRef
from a13n_stream_protocol.display import MAX_OBSERVATION_BYTES, ContentRef, DisplayContinuation, Item
from pydantic import Field, JsonValue

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.objects.interface import ObjectRef

INLINE_BYTES = 32 * 1024
PREVIEW_BYTES = 4 * 1024
MAX_CONTENT_BYTES = 16 * 1024 * 1024


class ContentObject(StoredRef):
    """Database-side dependency metadata. Object keys never cross the public API."""

    id: str
    size_bytes: int = Field(ge=0)
    media_type: Literal["text/plain", "application/json"]
    truncated: bool = False
    purpose: Literal["value", "continuation"] = "value"

    def public(self, preview: str) -> ContentRef:
        return ContentRef(
            id=self.id,
            size_bytes=self.size_bytes,
            media_type=self.media_type,
            preview=preview,
            truncated=self.truncated,
        )


def encode(value: JsonValue) -> tuple[bytes, Literal["text/plain", "application/json"]]:
    if isinstance(value, str):
        return value.encode("utf-8"), "text/plain"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), "application/json"


def decode(data: bytes, ref: ContentObject) -> JsonValue:
    if len(data) != ref.size_bytes:
        raise ServiceError(
            "unavailable", "Display content length does not match its reference", {"dependency": "objects"}
        )
    try:
        return data.decode("utf-8") if ref.media_type == "text/plain" else json.loads(data)
    except (UnicodeError, ValueError) as error:
        raise ServiceError("unavailable", "Display content is invalid", {"dependency": "objects"}) from error


@dataclass(frozen=True)
class _Saved:
    data: bytes
    ref: ContentObject


class Contents:
    """One attempt's field identities. Unchanged values keep their original immutable object reference."""

    def __init__(self, items: Sequence[Item] = (), refs: Sequence[ContentObject] = ()) -> None:
        self.refs = {ref.id: ref for ref in refs}
        self.saved: dict[tuple[str, str], _Saved] = {
            (item.id, field): _Saved(encode(item.content[field])[0], self.refs[ref.id])
            for item in items
            for field, ref in item.content_refs.items()
        }

    async def value(
        self, owner: str, field: str, value: JsonValue, write: Callable[[bytes], Awaitable[ObjectRef]]
    ) -> ContentObject | None:
        data, media_type = encode(value)
        previous = self.saved.get((owner, field))
        # A restored prefix may end a few bytes below the cap at a UTF-8 boundary. Never fill that
        # gap with a later suffix: bytes already discarded from the middle cannot be recovered.
        if previous is not None and previous.ref.truncated and data.startswith(previous.data):
            return previous.ref
        truncated = owner != "continuation" and len(data) > MAX_CONTENT_BYTES
        if truncated:
            data = data[:MAX_CONTENT_BYTES].decode("utf-8", errors="ignore").encode("utf-8")
            # An incomplete JSON document is a text prefix, not a valid structured value.
            media_type = "text/plain"
        if previous is not None and previous.data == data and previous.ref.media_type == media_type:
            # Crossing the cap after an exactly-at-cap snapshot must publish truncation metadata.
            if previous.ref.truncated == truncated:
                return previous.ref
        if len(data) <= INLINE_BYTES:
            return None
        stored = await write(data)
        ref = ContentObject(
            key=stored.key,
            digest=stored.digest,
            size=stored.size,
            id=new_object_id("cnt"),
            size_bytes=len(data),
            media_type=media_type,
            truncated=truncated,
            purpose="continuation" if owner == "continuation" else "value",
        )
        self.refs[ref.id] = ref
        self.saved[owner, field] = _Saved(data, ref)
        return ref

    async def item(self, item: Item, write: Callable[[bytes], Awaitable[ObjectRef]]) -> Item:
        content = dict(item.content)
        refs: dict[str, ContentRef] = {}
        for field, value in item.content.items():
            ref = await self.value(item.id, field, value, write)
            if ref is None:
                continue
            data = self.saved[item.id, field].data
            preview = data[:PREVIEW_BYTES].decode("utf-8", errors="ignore")
            refs[field] = ref.public(preview)
            # A JSON preview is not a partial JSON value. Keep it solely in the reference's preview.
            if ref.media_type == "text/plain":
                content[field] = preview
            else:
                del content[field]
        return item.model_copy(update={"content": content, "content_refs": refs})

    def dependencies(self, items: Sequence[Item]) -> tuple[ContentObject, ...]:
        ids = dict.fromkeys(ref.id for item in items for ref in item.content_refs.values())
        return tuple(self.refs[identifier] for identifier in ids)

    def retire(self, item_ids: Sequence[str]) -> None:
        retired = set(item_ids)
        self.saved = {key: value for key, value in self.saved.items() if key[0] not in retired}
        needed = {value.ref.id for value in self.saved.values()}
        self.refs = {key: value for key, value in self.refs.items() if key in needed}


def live_continuation(continuation: DisplayContinuation) -> DisplayContinuation:
    """The existing bounded client parser must not receive a full saved argument observation."""
    state = continuation.model_copy(deep=True)
    if state.arguments is not None and state.arguments.size > MAX_OBSERVATION_BYTES:
        state.arguments.event = None
    return state
