from __future__ import annotations

import hashlib

import pytest
from a13n_service.interactions.domain import RunPayloadObjectRef
from a13n_service.interactions.objects import (
    RunObjectIntegrityError,
    RunPayloadStore,
    RunStateStore,
    StaleStateWriter,
    validate_run_payload_reference,
)
from a13n_service.interactions.state import RunPayloadEnvelope, RunStateEnvelope
from a13n_service.storage import ObjectStore
from a13n_service.storage.codec import DurableObjectCodecError, decode_canonical_model
from pydantic import TypeAdapter

from .conftest import ORGANIZATION_ID, initial_state, progress_state

pytestmark = pytest.mark.anyio


async def test_state_create_claim_checkpoint_and_read_round_trip(
    interaction_object_store: ObjectStore,
) -> None:
    store = RunStateStore(interaction_object_store)
    initial = initial_state()

    created = await store.create(ORGANIZATION_ID, initial)
    checkpoint = await store.replace(
        created,
        progress_state(initial),
        run_attempt_id="rat_1234567890abcdef",
        fence=1,
    )
    restored = await store.read(ORGANIZATION_ID, initial.run_id, expected_thread_id=initial.thread_id)

    assert restored.envelope == checkpoint.envelope
    assert restored.info.version == checkpoint.info.version
    assert restored.writer_fence == 1
    assert restored.digest_sha256 == hashlib.sha256(restored.body).hexdigest()


async def test_object_version_and_fence_reject_stale_state_writers(
    interaction_object_store: ObjectStore,
) -> None:
    store = RunStateStore(interaction_object_store)
    initial = initial_state()
    created = await store.create(ORGANIZATION_ID, initial)
    first_checkpoint = await store.replace(
        created,
        progress_state(initial),
        run_attempt_id="rat_1234567890abcdef",
        fence=1,
    )
    with pytest.raises(StaleStateWriter, match="checkpoint committed"):
        await store.replace(
            created,
            progress_state(initial),
            run_attempt_id="rat_1234567890abcdef",
            fence=1,
        )

    second_attempt_id = "rat_abcdef1234567890"
    second_checkpoint = await store.replace(
        first_checkpoint,
        progress_state(first_checkpoint.envelope, fence=2, run_attempt_id=second_attempt_id),
        run_attempt_id=second_attempt_id,
        fence=2,
    )
    assert second_checkpoint.writer_fence == 2


async def test_state_read_rejects_metadata_fence_that_disagrees_with_envelope(
    interaction_object_store: ObjectStore,
) -> None:
    store = RunStateStore(interaction_object_store)
    created = await store.create(ORGANIZATION_ID, initial_state())
    metadata = dict(created.info.metadata)
    metadata["writer-fence"] = "1"
    await interaction_object_store.put(
        created.info.key,
        created.body,
        content_type=created.info.content_type,
        metadata=metadata,
        if_match=created.info.version,
    )

    with pytest.raises(RunObjectIntegrityError, match="writer fence does not match"):
        await store.read(ORGANIZATION_ID, created.envelope.run_id)


async def test_payload_is_content_addressed_and_idempotent(
    interaction_object_store: ObjectStore,
) -> None:
    store = RunPayloadStore(interaction_object_store)
    envelope = RunPayloadEnvelope(
        run_id="run_1234567890abcdef",
        payload_kind="input",
        payload_schema_version="1",
        payload={"text": "hello", "temperature": 1e-7},
    )

    first = await store.create(ORGANIZATION_ID, envelope)
    second = await store.create(ORGANIZATION_ID, envelope)

    assert first == second
    assert await store.read(ORGANIZATION_ID, first) == envelope
    assert await store.verify_reference(ORGANIZATION_ID, envelope.run_id, "input", first) == envelope


async def test_payload_read_rejects_wrong_organization(
    interaction_object_store: ObjectStore,
) -> None:
    store = RunPayloadStore(interaction_object_store)
    reference = await store.create(
        ORGANIZATION_ID,
        RunPayloadEnvelope(
            run_id="run_1234567890abcdef",
            payload_kind="output",
            payload_schema_version="1",
            payload={"answer": 42},
        ),
    )

    with pytest.raises(RunObjectIntegrityError, match="authorized organization"):
        await store.read("org_abcdef1234567890", reference)


def test_payload_reference_must_name_the_exact_run_owned_object() -> None:
    reference = RunPayloadObjectRef(
        object_key=(f"organizations/org_1234567890abcdef/runs/run_other1234567890/payloads/input/{'a' * 64}.json"),
        digest_sha256="a" * 64,
        size_bytes=123,
        content_type="application/vnd.converge.run-payload+json",
        schema_version="1",
    )

    with pytest.raises(RunObjectIntegrityError, match="owned by the selected Run"):
        validate_run_payload_reference(
            ORGANIZATION_ID,
            "run_1234567890abcdef",
            "input",
            reference,
        )


async def test_state_checkpoint_cas_is_portable_across_object_backends(
    object_store: ObjectStore,
) -> None:
    store = RunStateStore(object_store)
    initial = initial_state()
    created = await store.create(ORGANIZATION_ID, initial)
    checkpoint = await store.replace(
        created,
        progress_state(initial),
        run_attempt_id="rat_1234567890abcdef",
        fence=1,
    )

    assert checkpoint.info.version != created.info.version
    with pytest.raises(StaleStateWriter):
        await store.replace(
            created,
            progress_state(initial),
            run_attempt_id="rat_1234567890abcdef",
            fence=1,
        )


def test_codec_rejects_noncanonical_and_duplicate_json() -> None:
    adapter = TypeAdapter(RunStateEnvelope)
    with pytest.raises(DurableObjectCodecError, match="canonical"):
        decode_canonical_model(b'{"b":1,"a":2}', adapter)
    with pytest.raises(DurableObjectCodecError, match="strict UTF-8 JSON"):
        decode_canonical_model(b'{"a":1,"a":2}', adapter)
