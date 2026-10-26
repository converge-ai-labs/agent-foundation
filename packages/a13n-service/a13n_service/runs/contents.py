"""Bounded display values stored once per unchanged field; no chunking or content-based deduplication."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from a13n_harness import StoredRef
from a13n_logging import get_logger
from a13n_stream_protocol.display import MAX_OBSERVATION_BYTES, ContentRef, DisplayContinuation, Item
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.objects.interface import ObjectRef
from a13n_service.runs.mcp_apps.schemas import AppBinding

INLINE_BYTES = 32 * 1024
PREVIEW_BYTES = 4 * 1024
MAX_CONTENT_BYTES = 16 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PENDING_IMAGE_BYTES = 32 * 1024 * 1024
MAX_PENDING_IMAGES = 64
MAX_APP_BYTES = 8 * 1024 * 1024
MAX_APP_BINDING_BYTES = 64 * 1024
MAX_PENDING_APP_BYTES = 32 * 1024 * 1024
MAX_PENDING_APPS = 128
logger = get_logger(__name__)


class ContentObject(StoredRef):
    """Database-side dependency metadata. Object keys never cross the public API."""

    id: str
    size_bytes: int = Field(ge=0)
    media_type: Literal["text/plain", "application/json", "image/png", "image/jpeg"]
    truncated: bool = False
    purpose: Literal["value", "continuation", "image", "app"] = "value"
    app: AppBinding | None = None

    def public(self, preview: str) -> ContentRef:
        assert self.media_type == "text/plain" or self.media_type == "application/json"
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


class ImageRef(BaseModel):
    """An opaque screenshot identity; only the authenticated Run endpoint resolves it."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    media_type: Literal["image/png", "image/jpeg"]
    size_bytes: int = Field(ge=0)


def image_ids(item: Item) -> tuple[str, ...]:
    images = item.content.get("tool_images")
    if not isinstance(images, list):
        return ()
    return tuple(
        identifier for image in images if isinstance(image, dict) and isinstance(identifier := image.get("id"), str)
    )


def app_ids(item: Item) -> tuple[str, ...]:
    apps = item.content.get("mcp_apps")
    if not isinstance(apps, list):
        return ()
    return tuple(
        identifier
        for app in apps
        if isinstance(app, dict) and isinstance(identifier := app.get("app_id"), str) and not app.get("unavailable")
    )


class Contents:
    """One attempt's field identities. Unchanged values keep their original immutable object reference."""

    def __init__(self, items: Sequence[Item] = (), refs: Sequence[ContentObject] = ()) -> None:
        self.refs = {ref.id: ref for ref in refs}
        self.pending_apps: dict[str, tuple[bytes, AppBinding]] = {}
        self.unavailable_apps: set[str] = set()
        self.pending_app_bytes = 0
        self.app_items = {item.id: app_ids(item) for item in items}
        self.pending_images: dict[str, tuple[ImageRef, bytes]] = {}
        self.pending_image_bytes = 0
        self.image_items = {item.id: image_ids(item) for item in items}
        self.saved: dict[tuple[str, str], _Saved] = {
            (item.id, field): _Saved(encode(item.content[field])[0], self.refs[ref.id])
            for item in items
            for field, ref in item.content_refs.items()
        }

    def app(self, identifier: str, data: bytes, *, binding: AppBinding) -> bool:
        if (
            len(data) > MAX_APP_BYTES
            or len(binding.model_dump_json().encode()) > MAX_APP_BINDING_BYTES
            or self.pending_app_bytes + len(data) > MAX_PENDING_APP_BYTES
            or len(self.pending_apps) >= MAX_PENDING_APPS
        ):
            return False
        self.pending_apps[identifier] = (data, binding)
        self.pending_app_bytes += len(data)
        return True

    def image(self, data: bytes, media_type: Literal["image/png", "image/jpeg"]) -> ImageRef | None:
        # The synchronous producer observer cannot perform I/O or accumulate unbounded screenshots.
        if (
            len(data) > MAX_IMAGE_BYTES
            or self.pending_image_bytes + len(data) > MAX_PENDING_IMAGE_BYTES
            or len(self.pending_images) >= MAX_PENDING_IMAGES
        ):
            return None
        ref = ImageRef(id=new_object_id("cnt"), media_type=media_type, size_bytes=len(data))
        self.pending_images[ref.id] = (ref, data)
        self.pending_image_bytes += len(data)
        return ref

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
        self.app_items[item.id] = app_ids(item)
        for identifier in self.app_items[item.id]:
            pending_app = self.pending_apps.get(identifier)
            if pending_app is not None:
                data, binding = pending_app
                try:
                    stored = await write(data)
                except Exception:
                    # App presentation must not turn a completed business call into a failed attempt.
                    self.unavailable_apps.add(identifier)
                    logger.warning("MCP App original could not be saved", extra={"app_id": identifier})
                else:
                    self.refs[identifier] = ContentObject(
                        key=stored.key,
                        digest=stored.digest,
                        size=stored.size,
                        id=identifier,
                        size_bytes=len(data),
                        media_type="application/json",
                        purpose="app",
                        app=binding,
                    )
                del self.pending_apps[identifier]
                self.pending_app_bytes -= len(data)
        self.image_items[item.id] = image_ids(item)
        for identifier in self.image_items[item.id]:
            pending = self.pending_images.get(identifier)
            if pending is not None:
                image, data = pending
                stored = await write(data)
                self.refs[identifier] = ContentObject(
                    key=stored.key,
                    digest=stored.digest,
                    size=stored.size,
                    id=image.id,
                    size_bytes=image.size_bytes,
                    media_type=image.media_type,
                    purpose="image",
                )
                del self.pending_images[identifier]
                self.pending_image_bytes -= len(data)
        content = dict(item.content)
        apps = content.get("mcp_apps")
        if isinstance(apps, list):
            content["mcp_apps"] = [
                {**app, "unavailable": True}
                if isinstance(app, dict)
                and isinstance(app.get("app_id"), str)
                and app.get("app_id") in self.unavailable_apps
                else app
                for app in apps
            ]
        refs: dict[str, ContentRef] = {}
        for field, value in tuple(content.items()):
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
        ids = dict.fromkeys(
            identifier
            for item in items
            for identifier in (
                *[ref.id for ref in item.content_refs.values()],
                *(
                    identifier
                    for identifier in self.app_items.get(item.id, app_ids(item))
                    if identifier in self.refs and self.refs[identifier].purpose == "app"
                ),
                *(
                    identifier
                    for identifier in image_ids(item)
                    if identifier in self.refs and self.refs[identifier].purpose == "image"
                ),
            )
        )
        return tuple(self.refs[identifier] for identifier in ids)

    def retire(self, item_ids: Sequence[str]) -> None:
        retired = set(item_ids)
        self.saved = {key: value for key, value in self.saved.items() if key[0] not in retired}
        self.app_items = {key: value for key, value in self.app_items.items() if key not in retired}
        self.image_items = {key: value for key, value in self.image_items.items() if key not in retired}
        needed = {value.ref.id for value in self.saved.values()} | {
            identifier for ids in self.image_items.values() for identifier in ids
        }
        needed.update(identifier for ids in self.app_items.values() for identifier in ids)
        self.unavailable_apps.intersection_update(needed)
        self.refs = {key: value for key, value in self.refs.items() if key in needed}


def live_continuation(continuation: DisplayContinuation) -> DisplayContinuation:
    """The existing bounded client parser must not receive a full saved argument observation."""
    state = continuation.model_copy(deep=True)
    if state.arguments is not None and state.arguments.size > MAX_OBSERVATION_BYTES:
        state.arguments.event = None
    return state
