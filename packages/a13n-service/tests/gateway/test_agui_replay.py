from __future__ import annotations

import hashlib
import json

import pytest
import zstandard
from a13n_service.gateway.agui_replay import (
    HOSTED_AGUI_REPLAY_CONTENT_TYPE,
    HostedAguiReplayError,
    HostedAguiReplaySnapshot,
    HostedAguiReplayStore,
    HostedAguiReplayUnavailable,
    hosted_agui_replay_key,
)
from a13n_service.storage import ObjectStore
from a13n_service.storage.codec import COMPRESSED_JSON_ENCODING, canonical_model_bytes


@pytest.mark.anyio
@pytest.mark.parametrize("terminal_name", [None, "a13n.service.run_status"])
async def test_replay_round_trip_preserves_canonical_bytes(
    object_store: ObjectStore, terminal_name: str | None
) -> None:
    # Exercise the serialized contract independently of the current model.
    body = json.dumps(
        {
            "schema_version": "1",
            "binding_id": "binding_1",
            "run_id": "run_1",
            "external_thread_id": "external-thread",
            "external_run_id": "external-run",
            "agent_revision_id": "revision_1",
            "sealed_at": "2026-09-07T00:00:00Z",
            "events": [
                {
                    "ordinal": 0,
                    "event": {
                        "type": "RUN_STARTED",
                        "threadId": "external-thread",
                        "runId": "external-run",
                    },
                },
                {
                    "ordinal": 1,
                    "event": (
                        {"type": "CUSTOM", "name": terminal_name, "value": {"schema_version": "1", "status": "waiting"}}
                        if terminal_name is not None
                        else {"type": "RUN_FINISHED", "threadId": "external-thread", "runId": "external-run"}
                    ),
                },
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    objects = object_store
    key = hosted_agui_replay_key("org_1", "binding_1")
    writer = zstandard.ZstdCompressor(level=1, write_checksum=True).compressobj(size=len(body))
    split = len(body) // 2
    encoded = writer.compress(body[:split]) + writer.flush(zstandard.COMPRESSOBJ_FLUSH_BLOCK)
    encoded += writer.compress(body[split:]) + writer.flush()
    assert encoded != zstandard.ZstdCompressor(level=1, write_checksum=True).compress(body)
    info = await objects.put(
        key,
        encoded,
        content_type=HOSTED_AGUI_REPLAY_CONTENT_TYPE,
        metadata={
            "storage-encoding": COMPRESSED_JSON_ENCODING,
            "schema-version": "1",
            "binding-id": "binding_1",
            "run-id": "run_1",
            "digest-sha256": hashlib.sha256(encoded).hexdigest(),
        },
        if_none_match=True,
    )
    store = HostedAguiReplayStore(objects, max_events=10, max_bytes=4096)

    snapshot = await store.read("org_1", "binding_1")

    assert snapshot.run_id == "run_1"
    assert canonical_model_bytes(snapshot) == body
    assert await store.publish("org_1", snapshot) == snapshot
    assert await objects.stat(key) == info


@pytest.fixture
def replay_snapshot() -> HostedAguiReplaySnapshot:
    return HostedAguiReplaySnapshot.model_validate(
        {
            "binding_id": "binding_1",
            "run_id": "run_1",
            "external_thread_id": "external-thread",
            "external_run_id": "external-run",
            "agent_revision_id": "revision_1",
            "sealed_at": "2026-09-07T00:00:00Z",
            "events": [
                {
                    "ordinal": 0,
                    "event": {"type": "RUN_STARTED", "threadId": "external-thread", "runId": "external-run"},
                },
                {
                    "ordinal": 1,
                    "event": {"type": "RUN_FINISHED", "threadId": "external-thread", "runId": "external-run"},
                },
            ],
        }
    )


@pytest.mark.anyio
@pytest.mark.parametrize("corruption", ["encoding", "digest", "checksum", "run"])
async def test_hosted_replay_rejects_invalid_storage(object_store, replay_snapshot, corruption: str) -> None:
    store = HostedAguiReplayStore(object_store, max_events=10, max_bytes=4096)
    await store.publish("org_1", replay_snapshot)
    key = hosted_agui_replay_key("org_1", "binding_1")
    info = await object_store.stat(key)
    body = zstandard.ZstdCompressor(level=1, write_checksum=True).compress(canonical_model_bytes(replay_snapshot))
    metadata = dict(info.metadata)
    if corruption == "encoding":
        del metadata["storage-encoding"]
    elif corruption == "digest":
        metadata["digest-sha256"] = "0" * 64
    elif corruption == "run":
        metadata["run-id"] = "run_other"
    else:
        body = body[:-1] + bytes([body[-1] ^ 1])
        metadata["digest-sha256"] = hashlib.sha256(body).hexdigest()
    await object_store.put(key, body, content_type=info.content_type, metadata=metadata, if_match=info.version)
    with pytest.raises(HostedAguiReplayError):
        await store.read("org_1", "binding_1")


@pytest.mark.anyio
@pytest.mark.parametrize("bound", ["bytes", "events"])
async def test_hosted_replay_limits_apply_before_and_after_compression(
    object_store, replay_snapshot, bound: str
) -> None:
    store = HostedAguiReplayStore(object_store, max_events=10, max_bytes=4096)
    await store.publish("org_1", replay_snapshot)
    limited = HostedAguiReplayStore(
        object_store,
        max_events=1 if bound == "events" else 10,
        max_bytes=len(canonical_model_bytes(replay_snapshot)) - 1 if bound == "bytes" else 4096,
    )
    with pytest.raises(HostedAguiReplayUnavailable):
        await limited.publish("org_1", replay_snapshot)
    with pytest.raises(HostedAguiReplayUnavailable):
        await limited.read("org_1", "binding_1")
