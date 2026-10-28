"""Durable reclamation: rollback, interruption, pagination and reference safety."""

import asyncio
import json
from dataclasses import replace
from functools import partial

import pytest
from a13n_harness import HarnessState, StoredRef
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbox import Delivery, OutboxRow, claim
from a13n_service.runs import checkpoints
from a13n_service.runs.attempts import Lease
from a13n_service.runs.checkpoints import CLEANUP, Committed, RunState, StatePointer, TailPointer
from a13n_service.runs.display import Snapshot, StreamPosition, Tail
from a13n_service.runs.tables import RunRow
from sqlalchemy import func, select, update

pytestmark = pytest.mark.anyio

RUN_ID = "run_cleanup"


def _committed(tenant, sequence: int) -> Committed:  # type: ignore[no-untyped-def]
    def key(kind: checkpoints.ObjectKind) -> str:
        return f"{checkpoints.prefix(tenant.organization_id, RUN_ID, kind)}/1/{sequence:032x}"

    return Committed(
        state=StatePointer(key=key("state"), digest=f"{sequence:064x}", size=0, format=1, seq=sequence, attempt=1),
        tail=TailPointer(
            key=key("tail"),
            digest=f"{sequence + 100:064x}",
            size=0,
            format=1,
            position=StreamPosition(attempt=1, sequence=sequence),
            first=1,
            count=0,
        ),
    )


def _run(tenant, committed: Committed | None = None) -> RunRow:  # type: ignore[no-untyped-def]
    return RunRow(
        id=RUN_ID,
        organization_id=tenant.organization_id,
        workspace_id=tenant.workspace_id,
        checkpoint=committed.state.model_dump() if committed else None,
        tail=committed.tail.model_dump() if committed else None,
    )


async def _deliver(runtime) -> None:  # type: ignore[no-untyped-def]
    await Delivery(
        runtime.storage,
        {CLEANUP: partial(checkpoints.clean, runtime)},
        owner="test",
        policies=runtime.settings.outbox.policies,
    )()


async def test_commit_retires_replaced_objects_and_seal_keeps_the_pointers(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    previous, committed = _committed(tenant, 1), _committed(tenant, 2)
    run = _run(tenant, committed)
    for pointer in (previous.state, previous.tail, committed.state, committed.tail):
        await runtime.objects.put(pointer.key, b"", content_type="application/json")
    async with transaction(runtime.storage) as session:
        checkpoints.retire(session, run, previous, committed)
    await _deliver(runtime)
    assert await runtime.objects.get(previous.state.key) is None
    assert await runtime.objects.get(previous.tail.key) is None
    async with transaction(runtime.storage) as session:
        await checkpoints.reclaim(session, run)
    # A new sender can deliver the committed intent without any memory from the producer.
    await _deliver(replace(runtime))
    for pointer in (committed.state, committed.tail):
        assert await runtime.objects.get(pointer.key) == b""


async def test_rollback_does_not_delete_and_expired_claim_is_recovered(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    run = _run(tenant)
    key = f"{checkpoints.prefix(run.organization_id, run.id, 'state')}/orphan"
    await runtime.objects.put(key, b"", content_type="application/json")
    with pytest.raises(ValueError):
        async with transaction(runtime.storage) as session:
            await checkpoints.reclaim(session, run)
            raise ValueError("rollback")
    await _deliver(runtime)
    assert await runtime.objects.get(key) == b""
    async with transaction(runtime.storage) as session:
        await checkpoints.reclaim(session, run)
    (abandoned,) = await claim(
        runtime.storage, CLEANUP, owner="dead-process", limit=1, lease_seconds=60, max_attempts=12
    )
    async with transaction(runtime.storage) as session:
        await session.execute(update(OutboxRow).where(OutboxRow.id == abandoned.id).values(lease_expires_at=func.now()))
    await _deliver(replace(runtime))
    assert await runtime.objects.get(key) is None
    async with short_session(runtime.storage) as session:
        row = await session.get_one(OutboxRow, abandoned.id)
        assert (row.status, row.attempts) == ("delivered", 2)


async def test_scan_continues_past_a_page_and_keeps_the_tail_without_state(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    run = _run(tenant)
    run.tail = _committed(tenant, 1).tail.model_dump(mode="json")
    prefix = checkpoints.prefix(run.organization_id, run.id, "tail")
    kept = run.tail["key"]
    await runtime.objects.put(kept, b"", content_type="application/json")
    for i in range(1005):
        await runtime.objects.put(f"{prefix}/1/orphan-{i:04d}", b"", content_type="application/json")
    async with transaction(runtime.storage) as session:
        await checkpoints.reclaim(session, run)
    await _deliver(runtime)
    async with short_session(runtime.storage) as session:
        row = await session.scalar(select(OutboxRow).where(OutboxRow.kind == CLEANUP))
        assert row.status == "pending" and row.payload["after"] is not None and row.attempts == 0
    await _deliver(replace(runtime))
    assert await runtime.objects.keys(prefix, limit=2000) == [kept]
    async with short_session(runtime.storage) as session:
        row = await session.scalar(select(OutboxRow).where(OutboxRow.kind == CLEANUP))
        assert row.status == "delivered" and row.attempts == 1


async def test_takeover_scan_deletes_only_earlier_attempts_unreferenced_objects(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    run = _run(tenant)

    async def publish(attempt: int, kind: checkpoints.ObjectKind) -> StoredRef:
        lease = Lease(run.id, f"rat_{attempt}", "thr_test", run.organization_id, run.workspace_id, attempt, "w", "t")
        ref = await checkpoints.publish(runtime.objects, lease, kind, b"{}", level=3)
        return StoredRef(key=ref.key, digest=ref.digest, size=ref.size)

    kept, content, orphan_state, orphan_tail, orphan_content = (
        await publish(1, "state"),
        await publish(1, "contents"),
        await publish(1, "state"),
        await publish(1, "tail"),
        await publish(1, "subagents"),
    )
    run.checkpoint = StatePointer(
        key=kept.key, digest=kept.digest, size=kept.size, format=1, seq=1, attempt=1, refs=(content,)
    ).model_dump(mode="json")
    async with transaction(runtime.storage) as session:
        await checkpoints.reclaim(session, run, before_attempt=2)
    new = await publish(2, "tail")  # Published after the intent, not yet referenced by a row.
    await _deliver(runtime)
    for orphan in (orphan_state, orphan_tail, orphan_content):
        assert await runtime.objects.get(orphan.key) is None
    for live in (kept, content, new):
        assert await checkpoints.load(runtime.objects, live) == b"{}"


async def test_scan_budget_persists_progress_and_a_stalled_store_uses_retry_budget(
    runtime, tenant, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    runtime = replace(
        runtime,
        settings=runtime.settings.model_copy(
            update={"objects": runtime.settings.objects.model_copy(update={"timeout": 0.02})}
        ),
    )
    run = _run(tenant)
    prefix = checkpoints.prefix(run.organization_id, run.id, "state")
    for name in ("a", "b"):
        await runtime.objects.put(f"{prefix}/{name}", b"", content_type="application/json")
    original = runtime.objects.delete

    async def slow(key: str) -> None:
        if key.endswith("/b"):
            await asyncio.Event().wait()
        await original(key)

    monkeypatch.setattr(runtime.objects, "delete", slow)
    async with transaction(runtime.storage) as session:
        await checkpoints.reclaim(session, run)
    async with asyncio.timeout(2):
        await _deliver(runtime)
    async with short_session(runtime.storage) as session:
        row = await session.scalar(select(OutboxRow).where(OutboxRow.kind == CLEANUP))
        assert row.payload["after"] == f"{prefix}/a"
        assert row.status == "pending" and row.attempts == 0
    await _deliver(runtime)  # The next pass cannot progress, so it spends one failure attempt.
    async with short_session(runtime.storage) as session:
        row = await session.scalar(select(OutboxRow).where(OutboxRow.kind == CLEANUP))
        assert row.status == "pending" and row.attempts == 1 and row.last_error == "TimeoutError"
    monkeypatch.setattr(runtime.objects, "delete", original)
    async with transaction(runtime.storage) as session:
        await session.execute(update(OutboxRow).values(available_at=func.now()))
    await _deliver(runtime)
    assert await runtime.objects.keys(prefix, limit=10) == []


async def test_a_failed_publication_cannot_leave_another_write_behind_seal(runtime, tenant, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.infra.errors import ServiceError

    started, release = asyncio.Event(), asyncio.Event()
    original = runtime.objects.put

    async def put(key: str, data: bytes, *, content_type: str):  # type: ignore[no-untyped-def]
        if "/state/" in key:
            raise ServiceError("payload_too_large", "State cannot be stored")
        started.set()
        await release.wait()
        return await original(key, data, content_type=content_type)

    monkeypatch.setattr(runtime.objects, "put", put)
    lease = Lease("run_test", "rat_test", "thr_test", tenant.organization_id, tenant.workspace_id, 1, "worker", "token")
    state = RunState(harness=HarnessState.new(thread_id=lease.thread_id), seq=1, attempt=1)
    publishing = asyncio.create_task(checkpoints.publish_checkpoint(runtime, lease, state, Snapshot([], Tail())))
    try:
        await started.wait()
        await asyncio.sleep(0)
        assert not publishing.done()
        release.set()
        with pytest.raises(ServiceError, match="State cannot be stored"):
            await publishing
        # The caller can now seal knowing all this publication's bytes have landed.
        assert (
            len(await runtime.objects.keys(checkpoints.prefix(tenant.organization_id, lease.run_id, "tail"), limit=10))
            == 1
        )
    finally:
        release.set()
        await asyncio.gather(publishing, return_exceptions=True)


@pytest.mark.parametrize("format", [1, 2])
def test_legacy_checkpoint_defaults_to_no_session_hints(format):
    legacy = RunState(harness=HarnessState.new(thread_id="thr_legacy"), seq=1, attempt=1).model_dump(mode="json")
    legacy["format"] = format
    del legacy["envd_sessions"]
    restored = RunState.model_validate(legacy)
    assert restored.envd_sessions == {}
    pointer = StatePointer(key="legacy", digest="0" * 64, size=1, format=format, seq=1, attempt=1)
    assert checkpoints.require_compatible(RunRow(id="run_legacy", checkpoint=pointer.model_dump())) == pointer
    upgraded = RunState(harness=restored.harness, seq=2, attempt=2)
    assert upgraded.format == checkpoints.FORMAT == 4


@pytest.mark.parametrize("format", checkpoints.READABLE_FORMATS)
def test_checkpoint_preserves_unknown_fields_in_model_round_trip(format):
    payload = RunState(harness=HarnessState.new(thread_id="thr_extra"), seq=1, attempt=1).model_dump(mode="json")
    payload.update(format=format, extension={"items": [1, "value", None]})
    restored = RunState.model_validate(payload)
    assert restored.model_extra == {"extension": payload["extension"]}
    assert RunState.model_validate_json(restored.model_dump_json()).model_dump(mode="json") == payload
    assert json.loads(restored.to_json_bytes()) == payload
    assert RunState.model_validate_json(restored.to_json_bytes()) == restored


@pytest.mark.parametrize(
    "changes", [{"seq": 0}, {"attempt": 0}, {"format": checkpoints.FORMAT + 1}, {"envd_sessions": []}]
)
def test_checkpoint_extensions_do_not_relax_known_fields(changes):
    from pydantic import ValidationError

    payload = RunState(harness=HarnessState.new(thread_id="thr_extra"), seq=1, attempt=1).model_dump(mode="json")
    payload.update(extension={"accepted": True}, **changes)
    with pytest.raises(ValidationError):
        RunState.model_validate(payload)


async def test_checkpoint_bytes_publish_load_and_detect_corruption(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

    lease = Lease(
        "run_encoding", "rat_test", "thr_test", tenant.organization_id, tenant.workspace_id, 1, "worker", "token"
    )
    state = RunState(
        harness=HarnessState.new(
            thread_id=lease.thread_id,
            message_history=[
                ModelResponse(parts=[ToolCallPart("lookup", {"z": 1, "a": [None]}, tool_call_id="c1")]),
                ModelRequest(parts=[ToolReturnPart("lookup", {"value": "中文"}, tool_call_id="c1")]),
            ],
        ),
        seq=2,
        attempt=lease.number,
        deferred={"pending": [{"tool_call_id": "c2"}]},
        resume_input_consumed=True,
        future_run={"value": [1, None]},
    )
    ref = StoredRef(key="contents/kept", digest="a" * 64, size=42)
    state = state.model_copy(update={"harness": state.harness.model_copy(update={"refs": (ref,)})})
    messages = state.harness.message_history
    messages[0].parts.clear()
    pointer, display = await checkpoints.publish_checkpoint(runtime, lease, state, Snapshot([], Tail()))
    assert pointer.refs == (ref,)
    assert (pointer.seq, pointer.attempt, pointer.format) == (2, 1, checkpoints.FORMAT)
    assert json.loads(await checkpoints.load(runtime.objects, pointer)) == json.loads(state.model_dump_json())
    assert await checkpoints.load_state(runtime.objects, pointer) == state
    assert await checkpoints.load_tail(runtime.objects, display.tail) == Tail()
    await runtime.objects.put(pointer.key, b"corrupt", content_type="application/zstd")
    with pytest.raises(ServiceError, match="does not match its digest"):
        await checkpoints.load_state(runtime.objects, pointer)


async def test_checkpoint_load_validates_external_history(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    from pydantic import ValidationError

    lease = Lease(
        "run_invalid", "rat_test", "thr_test", tenant.organization_id, tenant.workspace_id, 1, "worker", "token"
    )
    payload = RunState(harness=HarnessState.new(), seq=1, attempt=1).model_dump(mode="json")
    payload["harness"]["message_history"] = [1]
    # Matching object digest does not replace schema validation on checkpoint import.
    ref = await checkpoints.publish(runtime.objects, lease, "state", json.dumps(payload).encode(), level=3)
    pointer = StatePointer(key=ref.key, digest=ref.digest, size=ref.size, format=checkpoints.FORMAT, seq=1, attempt=1)
    with pytest.raises(ValidationError):
        await checkpoints.load_state(runtime.objects, pointer)


@pytest.mark.parametrize("subclass", ["harness", "run"])
def test_checkpoint_bytes_keep_standard_subclass_serialization(subclass):
    from pydantic import model_serializer

    class ExtendedHarness(HarnessState):
        private_detail: str = "not in the annotated base envelope"

    class CustomRunState(RunState):
        @model_serializer
        def custom(self) -> dict[str, int]:
            return {"custom_sequence": self.seq}

    state = (
        RunState(harness=ExtendedHarness.new(), seq=1, attempt=1)
        if subclass == "harness"
        else CustomRunState(harness=HarnessState.new(), seq=2, attempt=1)
    )
    assert state.to_json_bytes() == state.model_dump_json().encode()
    if subclass == "harness":
        assert "private_detail" not in json.loads(state.to_json_bytes())["harness"]
    else:
        assert json.loads(state.to_json_bytes()) == {"custom_sequence": 2}
        unchecked = state.model_copy(
            update={"harness": state.harness.model_copy(update={"message_history_json": b"[1]"})}
        )
        assert unchecked.to_json_bytes() == state.to_json_bytes()
