"""Create-only retained delivery snapshots for Hosted AG-UI Runs."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Literal

from ag_ui.core import Event
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, TypeAdapter, model_validator

from a13n_service.storage import ObjectConflict, ObjectInfo, ObjectStore
from a13n_service.storage.codec import DurableObjectCodecError, canonical_model_bytes, decode_canonical_model

HOSTED_AGUI_REPLAY_CONTENT_TYPE = "application/vnd.a13n.hosted-agui-replay+json"
_EVENT = TypeAdapter(Event)
_ResourceId = Annotated[str, StringConstraints(min_length=1, max_length=72)]
_ExternalId = Annotated[str, StringConstraints(min_length=1, max_length=512)]


class HostedAguiReplayError(RuntimeError):
    """Retained Hosted AG-UI delivery is invalid or unavailable."""


class HostedAguiReplayUnavailable(HostedAguiReplayError):
    """A complete Hosted AG-UI delivery snapshot cannot be retained."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HostedAguiDeliveryEvent(_StrictModel):
    ordinal: int = Field(ge=0)
    event: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_standard_event(self) -> HostedAguiDeliveryEvent:
        _EVENT.validate_python(self.event)
        return self


class HostedAguiReplaySnapshot(_StrictModel):
    schema_version: Literal["1"] = "1"
    binding_id: _ResourceId
    service_run_id: _ResourceId
    external_thread_id: _ExternalId
    external_run_id: _ExternalId
    agent_revision_id: _ResourceId
    sealed_at: datetime
    events: tuple[HostedAguiDeliveryEvent, ...]

    @model_validator(mode="after")
    def validate_complete_delivery(self) -> HostedAguiReplaySnapshot:
        if self.sealed_at.tzinfo is None or self.sealed_at.utcoffset() is None:
            raise ValueError("Hosted AG-UI replay seal timestamp must include an offset")
        if not self.events:
            raise ValueError("Hosted AG-UI replay requires at least one event")
        if tuple(item.ordinal for item in self.events) != tuple(range(len(self.events))):
            raise ValueError("Hosted AG-UI replay ordinals must be contiguous from zero")
        if self.events[0].event.get("type") != "RUN_STARTED":
            raise ValueError("Hosted AG-UI replay must begin with RUN_STARTED")
        terminal = self.events[-1].event
        terminal_type = terminal.get("type")
        waiting = terminal_type == "CUSTOM" and terminal.get("name") == "a13n.foundation.run_status"
        if terminal_type not in {"RUN_FINISHED", "RUN_ERROR"} and not waiting:
            raise ValueError("Hosted AG-UI replay must end at a sealed delivery boundary")
        return self


_SNAPSHOT = TypeAdapter(HostedAguiReplaySnapshot)


class HostedAguiReplayStore:
    """Persist one complete immutable Hosted AG-UI delivery per binding."""

    def __init__(self, objects: ObjectStore, *, max_events: int, max_bytes: int) -> None:
        if min(max_events, max_bytes) < 1:
            raise ValueError("Hosted AG-UI replay bounds must be positive")
        self._objects = objects
        self._max_events = max_events
        self._max_bytes = max_bytes

    async def publish(
        self,
        organization_id: str,
        snapshot: HostedAguiReplaySnapshot,
    ) -> HostedAguiReplaySnapshot:
        if len(snapshot.events) > self._max_events:
            raise HostedAguiReplayUnavailable("Hosted AG-UI event count exceeds the retained replay bound")
        body = canonical_model_bytes(snapshot)
        if len(body) > self._max_bytes:
            raise HostedAguiReplayUnavailable("Hosted AG-UI replay exceeds its encoded size bound")
        digest = hashlib.sha256(body).hexdigest()
        key = hosted_agui_replay_key(organization_id, snapshot.binding_id)
        metadata = _metadata(snapshot, digest=digest)
        try:
            info = await self._objects.put(
                key,
                body,
                content_type=HOSTED_AGUI_REPLAY_CONTENT_TYPE,
                metadata=metadata,
                if_none_match=True,
            )
        except ObjectConflict as error:
            existing = await self.read(organization_id, snapshot.binding_id)
            if existing != snapshot:
                raise HostedAguiReplayError(
                    "existing Hosted AG-UI replay does not match the complete delivery"
                ) from error
            return existing
        _verify_info(info, key=key, body=body, metadata=metadata)
        return snapshot

    async def read(self, organization_id: str, binding_id: str) -> HostedAguiReplaySnapshot:
        key = hosted_agui_replay_key(organization_id, binding_id)
        body, info = await _read_object(self._objects, key, max_bytes=self._max_bytes)
        try:
            snapshot = decode_canonical_model(body, _SNAPSHOT)
        except DurableObjectCodecError as error:
            raise HostedAguiReplayError("Hosted AG-UI replay body is invalid") from error
        if snapshot.binding_id != binding_id:
            raise HostedAguiReplayError("Hosted AG-UI replay belongs to another binding")
        metadata = _metadata(snapshot, digest=hashlib.sha256(body).hexdigest())
        _verify_info(info, key=key, body=body, metadata=metadata)
        if len(snapshot.events) > self._max_events:
            raise HostedAguiReplayUnavailable("Hosted AG-UI event count exceeds the retained replay bound")
        return snapshot


def hosted_agui_replay_key(organization_id: str, binding_id: str) -> str:
    return f"organizations/{organization_id}/gateway/hosted-agui/{binding_id}/replay/version-1.json"


def _metadata(snapshot: HostedAguiReplaySnapshot, *, digest: str) -> dict[str, str]:
    return {
        "schema-version": "1",
        "binding-id": snapshot.binding_id,
        "run-id": snapshot.service_run_id,
        "digest-sha256": digest,
    }


async def _read_object(objects: ObjectStore, key: str, *, max_bytes: int) -> tuple[bytes, ObjectInfo]:
    async with objects.open(key) as reader:
        info = reader.info
        if info.size > max_bytes:
            raise HostedAguiReplayUnavailable("Hosted AG-UI replay exceeds its encoded size bound")
        chunks: list[bytes] = []
        size = 0
        async for chunk in reader:
            size += len(chunk)
            if size > max_bytes:
                raise HostedAguiReplayUnavailable("Hosted AG-UI replay exceeds its encoded size bound")
            chunks.append(chunk)
    body = b"".join(chunks)
    if len(body) != info.size:
        raise HostedAguiReplayError("Hosted AG-UI replay size does not match object metadata")
    return body, info


def _verify_info(info: ObjectInfo, *, key: str, body: bytes, metadata: Mapping[str, str]) -> None:
    if (
        info.key != key
        or info.size != len(body)
        or info.content_type != HOSTED_AGUI_REPLAY_CONTENT_TYPE
        or not info.version
        or any(info.metadata.get(name) != value for name, value in metadata.items())
    ):
        raise HostedAguiReplayError("Hosted AG-UI replay object metadata is invalid")


__all__ = [
    "HOSTED_AGUI_REPLAY_CONTENT_TYPE",
    "HostedAguiDeliveryEvent",
    "HostedAguiReplayError",
    "HostedAguiReplaySnapshot",
    "HostedAguiReplayStore",
    "HostedAguiReplayUnavailable",
    "hosted_agui_replay_key",
]
