from __future__ import annotations

import hashlib

import pytest
from a13n_service.interactions.domain import RunPayloadObjectRef, RunStatus, SealedRunState
from a13n_service.interactions.objects import (
    RunObjectIntegrityError,
    RunPayloadStore,
    RunStateStore,
    StaleStateWriter,
    validate_run_payload_reference,
)
from a13n_service.interactions.state import CompletedOutcomeCandidate, RunCheckpoint, RunPayloadEnvelope
from a13n_service.storage import ObjectStore, ObjectStoreUnavailable
from a13n_service.storage.codec import DurableObjectCodecError, decode_canonical_model
from pydantic import TypeAdapter

from .conftest import ORGANIZATION_ID, initial_state, progress_state
from .test_acceptance import _accepted_run

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
        attempt_number=1,
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
        attempt_number=1,
    )
    with pytest.raises(StaleStateWriter, match="replacement committed"):
        await store.replace(
            created,
            progress_state(initial),
            run_attempt_id="rat_1234567890abcdef",
            attempt_number=1,
        )

    second_attempt_id = "rat_abcdef1234567890"
    second_checkpoint = await store.replace(
        first_checkpoint,
        progress_state(first_checkpoint.envelope, attempt_number=2, run_attempt_id=second_attempt_id),
        run_attempt_id=second_attempt_id,
        attempt_number=2,
    )
    assert second_checkpoint.writer_fence == 2


async def test_state_read_rejects_metadata_fence_older_than_envelope(
    interaction_object_store: ObjectStore,
) -> None:
    store = RunStateStore(interaction_object_store)
    created = await store.create(ORGANIZATION_ID, initial_state())
    created = await store.replace(
        created, progress_state(created.envelope), run_attempt_id="rat_1234567890abcdef", attempt_number=1
    )
    metadata = dict(created.info.metadata)
    metadata["writer-fence"] = "0"
    await interaction_object_store.put(
        created.info.key,
        created.body,
        content_type=created.info.content_type,
        metadata=metadata,
        if_match=created.info.version,
    )

    with pytest.raises(RunObjectIntegrityError, match="writer fence is older"):
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
        attempt_number=1,
    )

    assert checkpoint.info.version != created.info.version
    with pytest.raises(StaleStateWriter):
        await store.replace(
            created,
            progress_state(initial),
            run_attempt_id="rat_1234567890abcdef",
            attempt_number=1,
        )


def test_codec_rejects_noncanonical_and_duplicate_json() -> None:
    adapter = TypeAdapter(RunCheckpoint)
    with pytest.raises(DurableObjectCodecError, match="canonical"):
        decode_canonical_model(b'{"b":1,"a":2}', adapter)
    with pytest.raises(DurableObjectCodecError, match="strict UTF-8 JSON"):
        decode_canonical_model(b'{"a":1,"a":2}', adapter)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("digest_sha256", "f" * 64),
        ("size_bytes", 1),
        ("content_type", "application/json"),
        ("envelope_schema_version", "3"),
        ("harness_schema_version", "unknown"),
        ("checkpoint_seq", 99),
    ],
)
async def test_run_state_read_verifies_every_selected_seal_field(
    interaction_object_store: ObjectStore, field: str, value: str | int
) -> None:
    store = RunStateStore(interaction_object_store)
    initial = await store.create(ORGANIZATION_ID, initial_state())
    envelope = progress_state(initial.envelope).model_copy(
        update={"checkpoint_kind": "completed", "outcome_candidate": CompletedOutcomeCandidate(output="done")}
    )
    state = await store.replace(
        initial,
        envelope,
        run_attempt_id=envelope.last_checkpoint_run_attempt_id,
        attempt_number=1,
    )
    run = _accepted_run(
        run_id=envelope.run_id, thread_id=envelope.thread_id, idempotency_key="sealed", request_fingerprint="a" * 64
    ).model_copy(
        update={
            "status": RunStatus.completed,
            "sealed_state": SealedRunState(
                digest_sha256=state.digest_sha256,
                size_bytes=len(state.body),
                content_type=state.info.content_type,
                envelope_schema_version=envelope.schema_version,
                harness_schema_version=envelope.harness_schema_version,
                checkpoint_seq=envelope.checkpoint_seq,
            ),
        }
    )
    assert await store.read_run(run) == state
    assert run.sealed_state is not None
    altered = run.model_copy(update={"sealed_state": run.sealed_state.model_copy(update={field: value})})
    with pytest.raises(RunObjectIntegrityError, match="sealed state"):
        await store.read_run(altered)


@pytest.mark.parametrize("operation", ["create", "checkpoint"])
@pytest.mark.parametrize("failure", [TimeoutError, ObjectStoreUnavailable])
async def test_state_write_lost_response_recovers_exact_committed_receipt(
    object_store: ObjectStore, monkeypatch, operation: str, failure: type[Exception]
) -> None:
    store = RunStateStore(object_store)
    initial = initial_state()
    state = None
    if operation != "create":
        state = await store.create(ORGANIZATION_ID, initial)
    put = object_store.put
    committed = []

    async def lose_response(*args, **kwargs):
        committed.append(await put(*args, **kwargs))
        raise failure("write response lost")

    monkeypatch.setattr(object_store, "put", lose_response)
    if operation == "create":
        result = await store.create(ORGANIZATION_ID, initial)
    else:
        assert state is not None
        result = await store.replace(
            state, progress_state(initial), run_attempt_id="rat_1234567890abcdef", attempt_number=1
        )
    assert len(committed) == 1
    assert result.info == committed[0]
    assert await store.read(ORGANIZATION_ID, initial.run_id) == result


async def test_uncommitted_write_failure_preserves_original_token(object_store: ObjectStore, monkeypatch) -> None:
    store = RunStateStore(object_store)
    original = await store.create(ORGANIZATION_ID, initial_state())

    async def fail_before_commit(*args, **kwargs):
        raise ObjectStoreUnavailable("not committed")

    monkeypatch.setattr(object_store, "put", fail_before_commit)
    with pytest.raises(ObjectStoreUnavailable, match="not committed"):
        await store.replace(
            original, progress_state(original.envelope), run_attempt_id="rat_1234567890abcdef", attempt_number=1
        )
    assert await store.read(ORGANIZATION_ID, original.envelope.run_id) == original


@pytest.mark.parametrize("changed", ["writer", "metadata"])
async def test_uncertain_write_never_adopts_another_writer_or_corrupt_metadata(
    object_store: ObjectStore, monkeypatch, changed: str
) -> None:
    store = RunStateStore(object_store)
    original = await store.create(ORGANIZATION_ID, initial_state())
    put = object_store.put

    async def change_after_commit(*args, **kwargs):
        info = await put(*args, **kwargs)
        if changed == "writer":
            monkeypatch.setattr(object_store, "put", put)
            current = await store.read(ORGANIZATION_ID, original.envelope.run_id)
            await store.replace(
                current,
                progress_state(current.envelope, attempt_number=2),
                run_attempt_id="rat_1234567890abcdef",
                attempt_number=2,
            )
        else:
            await put(
                info.key,
                args[1],
                content_type=info.content_type,
                metadata={**info.metadata, "writer-fence": "999"},
                if_match=info.version,
            )
        raise TimeoutError("write response lost")

    monkeypatch.setattr(object_store, "put", change_after_commit)
    with pytest.raises(StaleStateWriter):
        await store.replace(
            original, progress_state(original.envelope), run_attempt_id="rat_1234567890abcdef", attempt_number=1
        )


async def test_writer_claim_preserves_pending_input_and_fences_prior_versions(interaction_object_store):
    store = RunStateStore(interaction_object_store)
    initial = await store.create(ORGANIZATION_ID, initial_state())
    claimed = await store.claim_writer(initial, attempt_number=2)
    restored = await store.read(ORGANIZATION_ID, initial.envelope.run_id)
    assert restored == claimed
    assert restored.body == initial.body
    assert not restored.envelope.initial_input_applied
    assert restored.envelope.checkpoint_seq == 0
    assert restored.writer_fence == 2
    assert restored.info.version != initial.info.version
    with pytest.raises(StaleStateWriter):
        await store.replace(
            initial, progress_state(initial.envelope), run_attempt_id="rat_1234567890abcdef", attempt_number=1
        )
