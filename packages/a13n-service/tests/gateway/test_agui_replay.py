from __future__ import annotations

import hashlib
import json

import pytest
from a13n_service.gateway.agui_replay import (
    HOSTED_AGUI_REPLAY_CONTENT_TYPE,
    HostedAguiReplayStore,
    hosted_agui_replay_key,
)
from a13n_service.storage.codec import canonical_model_bytes
from a13n_service.storage.object_store import LocalObjectStore


@pytest.mark.anyio
async def test_reads_existing_version_one_replay_without_rewriting(tmp_path) -> None:
    # This is the persisted version-one representation, independent of the current model.
    body = json.dumps(
        {
            "schema_version": "1",
            "binding_id": "binding_1",
            "foundation_run_id": "run_1",
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
                    "event": {
                        "type": "RUN_FINISHED",
                        "threadId": "external-thread",
                        "runId": "external-run",
                    },
                },
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    objects = await LocalObjectStore.create(tmp_path / "objects")
    key = hosted_agui_replay_key("org_1", "binding_1")
    await objects.put(
        key,
        body,
        content_type=HOSTED_AGUI_REPLAY_CONTENT_TYPE,
        metadata={
            "schema-version": "1",
            "binding-id": "binding_1",
            "run-id": "run_1",
            "digest-sha256": hashlib.sha256(body).hexdigest(),
        },
        if_none_match=True,
    )
    store = HostedAguiReplayStore(objects, max_events=10, max_bytes=4096)

    snapshot = await store.read("org_1", "binding_1")

    assert snapshot.foundation_run_id == "run_1"
    assert canonical_model_bytes(snapshot) == body
    assert await store.publish("org_1", snapshot) == snapshot
