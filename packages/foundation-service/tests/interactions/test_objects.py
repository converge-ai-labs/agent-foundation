from __future__ import annotations

import asyncio
import hashlib

import pytest
import rfc8785
from a13n_service.interactions.domain import RunPayloadObjectRef
from a13n_service.interactions.objects import (
    RunObjectError,
    RunObjectIntegrityError,
    RunPayloadStore,
    RunStateStore,
    StaleStateWriter,
    validate_run_payload_reference,
)
from a13n_service.interactions.state import CompletedOutcomeCandidate, RunPayloadEnvelope, RunStateEnvelope
from a13n_service.storage import ObjectStore
from a13n_service.storage.codec import DurableObjectCodecError, decode_canonical_model
from pydantic import TypeAdapter

from .conftest import TENANT_ID, initial_state, progress_state

pytestmark = pytest.mark.anyio


async def test_state_create_claim_checkpoint_and_read_round_trip(
    interaction_object_store: ObjectStore,
) -> None:
    store = RunStateStore(interaction_object_store)
    initial = initial_state()

    created = await store.create(TENANT_ID, initial)
    checkpoint = await store.replace(
        await store.claim_writer(created, fence=1),
        progress_state(initial),
        run_attempt_id="rat_1234567890abcdef",
        fence=1,
    )
    restored = await store.read(TENANT_ID, initial.run_id, expected_thread_id=initial.thread_id)

    assert restored.envelope == checkpoint.envelope
    assert restored.info.version == checkpoint.info.version
    assert restored.writer_fence == 1
    assert restored.digest_sha256 == hashlib.sha256(restored.body).hexdigest()


async def test_object_version_and_fence_reject_stale_state_writers(
    interaction_object_store: ObjectStore,
) -> None:
    store = RunStateStore(interaction_object_store)
    initial = initial_state()
    created = await store.create(TENANT_ID, initial)
    created = await store.claim_writer(created, fence=1)
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
        await store.claim_writer(first_checkpoint, fence=2),
        progress_state(first_checkpoint.envelope, fence=2, run_attempt_id=second_attempt_id),
        run_attempt_id=second_attempt_id,
        fence=2,
    )
    assert second_checkpoint.writer_fence == 2


@pytest.mark.parametrize("metadata_fence", ["0", "2", "01", "invalid"])
async def test_state_read_rejects_metadata_fence_different_from_body(
    interaction_object_store: ObjectStore,
    metadata_fence: str,
) -> None:
    store = RunStateStore(interaction_object_store)
    created = await store.create(TENANT_ID, initial_state())
    created = await store.replace(
        await store.claim_writer(created, fence=1),
        progress_state(created.envelope),
        run_attempt_id="rat_1234567890abcdef",
        fence=1,
    )
    metadata = dict(created.info.metadata)
    metadata["writer-fence"] = metadata_fence
    await interaction_object_store.put(
        created.info.key,
        created.body,
        content_type=created.info.content_type,
        metadata=metadata,
        if_match=created.info.version,
    )

    with pytest.raises(RunObjectIntegrityError, match="writer-fence"):
        await store.read(TENANT_ID, created.envelope.run_id)


async def test_writer_claim_preserves_checkpoint_and_prevents_previous_owner_writes(
    object_store: ObjectStore,
) -> None:
    store = RunStateStore(object_store)
    initial = initial_state()
    created = await store.create(TENANT_ID, initial)
    first = await store.claim_writer(created, fence=1)
    second = await store.claim_writer(first, fence=2)
    with pytest.raises(StaleStateWriter):
        await store.replace(first, progress_state(initial), run_attempt_id="rat_1234567890abcdef", fence=1)
    assert first.info.version != created.info.version
    assert second.info.version != first.info.version
    assert len({second.body, first.body, created.body}) == 3
    assert second.envelope.model_dump(exclude={"writer_fence"}) == initial.model_dump(exclude={"writer_fence"})
    assert second.envelope.checkpoint_seq == 0
    assert second.writer_fence == 2
    assert (await store.read(TENANT_ID, initial.run_id)).writer_fence == 2
    with pytest.raises(StaleStateWriter):
        await store.claim_writer(second, fence=1)
    checkpoint = await store.replace(
        second,
        progress_state(initial, fence=2, run_attempt_id="rat_abcdef1234567890"),
        run_attempt_id="rat_abcdef1234567890",
        fence=2,
    )
    assert checkpoint.envelope.checkpoint_seq == 1
    assert checkpoint.writer_fence == 2


async def test_competing_writer_claims_use_exact_object_version(object_store: ObjectStore) -> None:
    store = RunStateStore(object_store)
    created = await store.create(TENANT_ID, initial_state())
    results = await asyncio.gather(
        store.claim_writer(created, fence=1),
        store.claim_writer(created, fence=2),
        return_exceptions=True,
    )
    assert sum(isinstance(result, StaleStateWriter) for result in results) == 1
    current = await store.read(TENANT_ID, created.envelope.run_id)
    assert current.writer_fence in {1, 2}
    assert current.info.version != created.info.version
    assert current.info.metadata["writer-fence"] == str(current.envelope.writer_fence)
    assert current.digest_sha256 == hashlib.sha256(current.body).hexdigest()
    with pytest.raises(StaleStateWriter):
        await store.claim_writer(created, fence=3)


async def test_reconciled_same_fence_claim_preserves_state_and_rejects_stale_token(object_store: ObjectStore) -> None:
    store = RunStateStore(object_store)
    created = await store.create(TENANT_ID, initial_state())
    committed = await store.claim_writer(created, fence=1)
    # Reconcile from storage as after a committed write with a lost acknowledgement.
    current = await store.read(TENANT_ID, created.envelope.run_id)
    repeated = await store.claim_writer(current, fence=1)
    assert repeated.body == committed.body
    assert repeated.envelope == committed.envelope
    assert repeated.digest_sha256 == committed.digest_sha256
    await store.claim_writer(repeated, fence=2)
    with pytest.raises(StaleStateWriter):
        await store.claim_writer(repeated, fence=1)


@pytest.mark.parametrize("completed", [False, True])
async def test_takeover_preserves_checkpoint_provenance_and_prepared_outcome(
    object_store: ObjectStore, *, completed: bool
) -> None:
    store = RunStateStore(object_store)
    initial = initial_state()
    claimed = await store.claim_writer(await store.create(TENANT_ID, initial), fence=1)
    candidate = progress_state(initial)
    if completed:
        payload = candidate.model_dump(mode="python")
        payload.update(checkpoint_kind="completed", outcome_candidate=CompletedOutcomeCandidate(output="done"))
        candidate = RunStateEnvelope.model_validate(payload)
    published = await store.replace(claimed, candidate, run_attempt_id="rat_1234567890abcdef", fence=1)
    taken = await store.claim_writer(published, fence=2)
    assert taken.envelope.model_dump(exclude={"writer_fence"}) == candidate.model_dump(exclude={"writer_fence"})
    assert taken.envelope.last_checkpoint_fence == 1
    assert taken.writer_fence == 2
    assert taken.digest_sha256 != published.digest_sha256
    assert taken.info.size == len(taken.body)
    assert taken.info.metadata["digest-sha256"] == taken.digest_sha256
    assert (await store.read(TENANT_ID, initial.run_id)) == taken
    with pytest.raises(StaleStateWriter):
        await store.claim_writer(published, fence=1)


@pytest.mark.parametrize("schema_version", ["1", "2"])
async def test_state_read_rejects_missing_writer_field_without_inferred_migration(
    interaction_object_store: ObjectStore, schema_version: str
) -> None:
    store = RunStateStore(interaction_object_store)
    created = await store.create(TENANT_ID, initial_state())
    payload = created.envelope.model_dump(mode="json", exclude={"writer_fence"})
    payload["schema_version"] = schema_version
    body = rfc8785.dumps(payload)
    metadata = dict(created.info.metadata)
    metadata.update({"schema-version": schema_version, "digest-sha256": hashlib.sha256(body).hexdigest()})
    await interaction_object_store.put(
        created.info.key, body, content_type=created.info.content_type, metadata=metadata, if_match=created.info.version
    )
    with pytest.raises(RunObjectIntegrityError, match="body is invalid"):
        await store.read(TENANT_ID, created.envelope.run_id)


async def test_checkpoint_requires_writer_claim(interaction_object_store: ObjectStore) -> None:
    store = RunStateStore(interaction_object_store)
    initial = initial_state()
    created = await store.create(TENANT_ID, initial)
    with pytest.raises(StaleStateWriter, match="must claim"):
        await store.replace(created, progress_state(initial), run_attempt_id="rat_1234567890abcdef", fence=1)


async def test_creation_cannot_import_another_writer_claim(interaction_object_store: ObjectStore) -> None:
    store = RunStateStore(interaction_object_store)
    initial = initial_state().model_copy(update={"writer_fence": 1})
    with pytest.raises(ValueError, match="writer fence zero"):
        await store.create(TENANT_ID, initial)


async def test_writer_claim_checks_expanded_body_size_before_publication(interaction_object_store: ObjectStore) -> None:
    store = RunStateStore(interaction_object_store)
    created = await store.create(TENANT_ID, initial_state())
    bounded = RunStateStore(interaction_object_store, max_state_bytes=len(created.body))
    with pytest.raises(RunObjectError, match="size limit"):
        await bounded.claim_writer(created, fence=10)
    assert await store.read(TENANT_ID, created.envelope.run_id) == created


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

    first = await store.create(TENANT_ID, envelope)
    second = await store.create(TENANT_ID, envelope)

    assert first == second
    assert await store.read(TENANT_ID, first) == envelope
    assert await store.verify_reference(TENANT_ID, envelope.run_id, "input", first) == envelope


async def test_payload_read_rejects_wrong_tenant(
    interaction_object_store: ObjectStore,
) -> None:
    store = RunPayloadStore(interaction_object_store)
    reference = await store.create(
        TENANT_ID,
        RunPayloadEnvelope(
            run_id="run_1234567890abcdef",
            payload_kind="output",
            payload_schema_version="1",
            payload={"answer": 42},
        ),
    )

    with pytest.raises(RunObjectIntegrityError, match="authorized tenant"):
        await store.read("org_abcdef1234567890", reference)


def test_payload_reference_must_name_the_exact_run_owned_object() -> None:
    reference = RunPayloadObjectRef(
        object_key=(f"tenants/org_1234567890abcdef/runs/run_other1234567890/payloads/input/{'a' * 64}.json"),
        digest_sha256="a" * 64,
        size_bytes=123,
        content_type="application/vnd.converge.run-payload+json",
        schema_version="1",
    )

    with pytest.raises(RunObjectIntegrityError, match="owned by the selected Run"):
        validate_run_payload_reference(
            TENANT_ID,
            "run_1234567890abcdef",
            "input",
            reference,
        )


async def test_state_checkpoint_cas_is_portable_across_object_backends(
    object_store: ObjectStore,
) -> None:
    store = RunStateStore(object_store)
    initial = initial_state()
    created = await store.create(TENANT_ID, initial)
    checkpoint = await store.replace(
        await store.claim_writer(created, fence=1),
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
