"""Durable reclamation: rollback, interruption, pagination and reference safety."""

import asyncio
from dataclasses import replace
from functools import partial

import pytest
from a13n_harness import HarnessState
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.outbox import Delivery, OutboxRow, claim
from a13n_service.runs import checkpoints
from a13n_service.runs.attempts import Lease
from a13n_service.runs.checkpoints import CLEANUP, Committed, DisplayPointer, RunState, StatePointer
from a13n_service.runs.display import StreamPosition
from a13n_service.runs.tables import RunRow
from sqlalchemy import func, select, update

pytestmark = pytest.mark.anyio

RUN_ID = "run_cleanup"


def _committed(tenant, sequence: int) -> Committed:  # type: ignore[no-untyped-def]
    def key(kind: checkpoints.ObjectKind) -> str:
        return f"{checkpoints.prefix(tenant.organization_id, RUN_ID, kind)}/1/{sequence:032x}"

    return Committed(
        state=StatePointer(key=key("state"), digest=f"{sequence:064x}", size=0, format=1, seq=sequence, attempt=1),
        display=DisplayPointer(
            key=key("display"),
            digest=f"{sequence + 100:064x}",
            size=0,
            format=1,
            position=StreamPosition(attempt=1, sequence=sequence),
        ),
    )


def _run(tenant, committed: Committed | None = None) -> RunRow:  # type: ignore[no-untyped-def]
    return RunRow(
        id=RUN_ID,
        organization_id=tenant.organization_id,
        workspace_id=tenant.workspace_id,
        checkpoint=committed.state.model_dump() if committed else None,
        display=committed.display.model_dump() if committed else None,
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
    for pointer in (previous.state, previous.display, committed.state, committed.display):
        await runtime.objects.put(pointer.key, b"", content_type="application/json")
    async with transaction(runtime.storage) as session:
        checkpoints.retire(session, run, previous, committed)
    await _deliver(runtime)
    assert await runtime.objects.get(previous.state.key) is None
    assert await runtime.objects.get(previous.display.key) is None
    async with transaction(runtime.storage) as session:
        checkpoints.reclaim(session, run)
    # A new sender can deliver the committed intent without any memory from the producer.
    await _deliver(replace(runtime))
    for pointer in (committed.state, committed.display):
        assert await runtime.objects.get(pointer.key) == b""


async def test_rollback_does_not_delete_and_expired_claim_is_recovered(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    run = _run(tenant)
    key = f"{checkpoints.prefix(run.organization_id, run.id, 'state')}/orphan"
    await runtime.objects.put(key, b"", content_type="application/json")
    with pytest.raises(ValueError):
        async with transaction(runtime.storage) as session:
            checkpoints.reclaim(session, run)
            raise ValueError("rollback")
    await _deliver(runtime)
    assert await runtime.objects.get(key) == b""
    async with transaction(runtime.storage) as session:
        checkpoints.reclaim(session, run)
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


async def test_scan_continues_past_a_page_and_keeps_display_without_state(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    run = _run(tenant)
    run.display = _committed(tenant, 1).display.model_dump(mode="json")
    prefix = checkpoints.prefix(run.organization_id, run.id, "display")
    kept = run.display["key"]
    await runtime.objects.put(kept, b"", content_type="application/json")
    for i in range(1005):
        await runtime.objects.put(f"{prefix}/1/orphan-{i:04d}", b"", content_type="application/json")
    async with transaction(runtime.storage) as session:
        checkpoints.reclaim(session, run)
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

    async def publish(attempt: int, kind: checkpoints.ObjectKind) -> str:
        lease = Lease(run.id, f"rat_{attempt}", "thr_test", run.organization_id, run.workspace_id, attempt, "w", "t")
        return (await checkpoints.publish(runtime.objects, lease, kind, b"{}")).key

    kept, orphan_state, orphan_display = (
        await publish(1, "state"),
        await publish(1, "state"),
        await publish(1, "display"),
    )
    run.checkpoint = StatePointer(key=kept, digest="0" * 64, size=2, format=1, seq=1, attempt=1).model_dump()
    async with transaction(runtime.storage) as session:
        checkpoints.reclaim(session, run, before_attempt=2)
    new = await publish(2, "display")  # Published after the intent, not yet referenced by a row.
    await _deliver(runtime)
    assert await runtime.objects.get(orphan_state) is None
    assert await runtime.objects.get(orphan_display) is None
    assert await runtime.objects.get(kept) is not None
    assert await runtime.objects.get(new) is not None


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
        checkpoints.reclaim(session, run)
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
    from a13n_service.runs.display import Display

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
    publishing = asyncio.create_task(checkpoints.publish_checkpoint(runtime, lease, state, Display()))
    try:
        await started.wait()
        await asyncio.sleep(0)
        assert not publishing.done()
        release.set()
        with pytest.raises(ServiceError, match="State cannot be stored"):
            await publishing
        # The caller can now seal knowing all this publication's bytes have landed.
        assert (
            len(
                await runtime.objects.keys(
                    checkpoints.prefix(tenant.organization_id, lease.run_id, "display"), limit=10
                )
            )
            == 1
        )
    finally:
        release.set()
        await asyncio.gather(publishing, return_exceptions=True)
