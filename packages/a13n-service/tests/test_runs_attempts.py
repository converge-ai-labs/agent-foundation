"""Attempts: the worker loop, claims, lease renewal and takeover, checkpoint commits and usage ingestion."""

import asyncio
import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from a13n_harness.usage import ModelUsageRecord, UsageSnapshot
from a13n_service.infra.db import transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.models import service as models_service
from a13n_service.runs import checkpoints
from a13n_service.runs import execute as execute_module
from a13n_service.runs import seal as seal_module
from a13n_service.runs import worker as worker_module
from a13n_service.runs.attempts import AttemptControl, AuthorityRevoked, Lease, LeaseLost, renew
from a13n_service.runs.checkpoints import FORMAT
from a13n_service.runs.claim import claim
from a13n_service.runs.execute import execute
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Outcome
from a13n_service.runs.seal import expire_leases, seal_attempt
from a13n_service.runs.tables import AttemptRow, RunRow, UsageRecordRow
from a13n_service.runs.usage import UsageBuffer, UsageReport, ingest_late
from a13n_service.runs.worker import Worker
from a13n_service.tenancy.access import RoleGrant
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.tables import GrantRow
from sqlalchemy import delete, event, func, select, text, update

pytestmark = pytest.mark.anyio

LEASE = Lease(
    run_id="run_test",
    attempt_id="rat_test",
    thread_id="thread_test",
    organization_id="org_test",
    workspace_id="ws_test",
    number=1,
    worker_id="worker-test",
    token="token",
)


def _with_worker(runtime: Runtime, **worker: object) -> Runtime:
    settings = runtime.settings
    return replace(runtime, settings=settings.model_copy(update={"worker": settings.worker.model_copy(update=worker)}))


async def _until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(10):
        while not condition():
            await asyncio.sleep(0.01)


async def test_a_claim_failure_keeps_the_worker_and_its_running_attempts(runtime, monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    # Renewal is not due within the test, so only the claim loop is exercised.
    runtime = _with_worker(runtime, scan_seconds=0.01, authority_seconds=5)
    claims = 0

    async def flaky_claim(*args, **kwargs) -> list[Lease]:  # type: ignore[no-untyped-def]
        nonlocal claims
        claims += 1
        if claims == 2:
            raise ConnectionError("secret_database_address")
        return [LEASE] if claims == 1 else []

    started, finish = asyncio.Event(), asyncio.Event()

    async def attempt(runtime, lease, control) -> None:  # type: ignore[no-untyped-def]
        started.set()
        await finish.wait()

    monkeypatch.setattr(worker_module, "claim", flaky_claim)
    worker = Worker(runtime, attempt)
    loop = asyncio.create_task(worker.run())
    await started.wait()
    await _until(lambda: claims >= 4)
    assert not loop.done() and LEASE.attempt_id in worker.running
    [failure] = [record for record in caplog.records if record.getMessage() == "Claim failed"]
    assert failure.exception_details[0]["frames"][-1]["function"] == "flaky_claim"
    assert "secret_database_address" not in json.dumps(vars(failure))

    finish.set()
    await _until(lambda: not worker.running)
    loop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop


async def test_a_renewal_that_never_answers_stops_the_attempt(runtime, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = _with_worker(runtime, lease_seconds=3, authority_seconds=0.05, scan_seconds=5)

    async def hanging_renew(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        await asyncio.Event().wait()

    unclaimed = [LEASE]

    async def claim_once(*args, **kwargs) -> list[Lease]:  # type: ignore[no-untyped-def]
        return [unclaimed.pop()] if unclaimed else []

    stopped = asyncio.Event()

    async def attempt(runtime, lease, control) -> None:  # type: ignore[no-untyped-def]
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.set()
            raise

    monkeypatch.setattr(worker_module, "claim", claim_once)
    monkeypatch.setattr(worker_module, "renew", hanging_renew)
    worker = Worker(runtime, attempt)
    loop = asyncio.create_task(worker.run())
    # The lease can no longer be renewed in time once a third of it is left: the attempt stops then.
    async with asyncio.timeout(5):
        await stopped.wait()
    await _until(lambda: not worker.running)
    loop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop


async def test_one_heartbeat_serves_every_attempt_and_a_missed_renewal_cancels_only_its_own(
    runtime, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    # The renewed attempt ignores its stop, so the final cancel drains only briefly.
    runtime = _with_worker(runtime, slots=2, lease_seconds=1, authority_seconds=0.01, scan_seconds=5, drain_seconds=0.1)
    renewed, missed = LEASE, replace(LEASE, run_id="run_missed", attempt_id="rat_missed")
    unclaimed = [renewed, missed]

    async def claim_all(*args, **kwargs) -> list[Lease]:  # type: ignore[no-untyped-def]
        claimed = list(unclaimed)
        unclaimed.clear()
        return claimed

    heartbeats: list[list[Lease]] = []

    async def renewing(storage, access, leases, *, extend, seconds) -> tuple[dict[str, Outcome], set[str]]:  # type: ignore[no-untyped-def]
        heartbeats.append(leases)
        return {renewed.attempt_id: Outcome.cancelled()}, set(extend) - {missed.attempt_id}

    controls: dict[str, AttemptControl] = {}
    cancellations = 0
    cleanup, cleaned = asyncio.Event(), asyncio.Event()

    async def attempt(runtime, lease, control) -> None:  # type: ignore[no-untyped-def]
        nonlocal cancellations
        controls[lease.attempt_id] = control
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellations += 1
            # Heartbeats go on during this cleanup; cancelling it again would interrupt it.
            await cleanup.wait()
            cleaned.set()
            raise

    monkeypatch.setattr(worker_module, "claim", claim_all)
    monkeypatch.setattr(worker_module, "renew", renewing)
    worker = Worker(runtime, attempt)
    loop = asyncio.create_task(worker.run())
    try:
        await _until(lambda: cancellations == 1)
        count = len(heartbeats)
        await _until(lambda: len(heartbeats) >= count + 5)
        cleanup.set()
        async with asyncio.timeout(5):
            await cleaned.wait()
        await _until(lambda: set(worker.running) == {renewed.attempt_id})
        assert cancellations == 1
        assert {lease.attempt_id for lease in heartbeats[0]} == {renewed.attempt_id, missed.attempt_id}
        control = controls[renewed.attempt_id]
        assert control.stopped.is_set() and control.outcome == Outcome.cancelled()
        assert not control.renewal_missed()
    finally:
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)


async def test_heartbeats_continue_while_the_worker_drains(runtime, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = _with_worker(runtime, authority_seconds=0.01, scan_seconds=5, drain_seconds=5)
    unclaimed = [LEASE]

    async def claim_once(*args, **kwargs) -> list[Lease]:  # type: ignore[no-untyped-def]
        return [unclaimed.pop()] if unclaimed else []

    heartbeats = 0

    async def renewing(*args, **kwargs) -> tuple[dict[str, Outcome], set[str]]:  # type: ignore[no-untyped-def]
        nonlocal heartbeats
        heartbeats += 1
        return {}, set()

    started, handed_off, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def attempt(runtime, lease, control) -> None:  # type: ignore[no-untyped-def]
        started.set()
        await control.handoff.wait()
        handed_off.set()
        await finish.wait()

    monkeypatch.setattr(worker_module, "claim", claim_once)
    monkeypatch.setattr(worker_module, "renew", renewing)
    loop = asyncio.create_task(Worker(runtime, attempt).run())
    try:
        await started.wait()
        loop.cancel()
        await handed_off.wait()
        count = heartbeats
        await _until(lambda: heartbeats >= count + 3)
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await loop
    finally:
        finish.set()
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)


async def test_no_call_is_sent_once_the_lease_missed_its_renewal(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    scripted_model.say("never sent")
    (lease,) = await claim(service.runtime, worker_id="worker-test", worker_build="test", limit=1)
    # Long enough for checkpoint writes, shorter than the renewal margin: the last renewal was missed.
    control = AttemptControl(deadline=asyncio.get_running_loop().time() + 7, renewal_margin=10)

    with pytest.raises(LeaseLost):
        await execute(service.runtime, lease, control)
    assert scripted_model.requests.empty()
    # The attempt left the run to lease expiry and recovery.
    assert (await runs_kit.get_run(service, run_id))["status"] == "running"


async def test_two_workers_claim_a_run_once(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    claimed = await asyncio.gather(
        *(claim(service.runtime, worker_id=f"worker-{n}", worker_build="test", limit=4) for n in range(2))
    )
    assert [lease.run_id for leases in claimed for lease in leases] == [run_id]
    attempts = (await service.client.get(f"{service.api}/runs/{run_id}/attempts")).json()["items"]
    assert [item["status"] for item in attempts] == ["leased"]


async def test_claim_leaves_newer_checkpoints_and_fails_older_ones(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    newer = (await runs_kit.start_thread(service, agent, "newer"))["run"]["id"]
    older = (await runs_kit.start_thread(service, agent, "older"))["run"]["id"]
    pointer = {"digest": "0" * 64, "size": 1}
    async with transaction(service.runtime.storage) as session:
        for run_id, format in ((newer, FORMAT + 1), (older, FORMAT - 1)):
            await session.execute(
                update(RunRow)
                .where(RunRow.id == run_id)
                .values(
                    checkpoint={**pointer, "format": format, "seq": 1, "attempt": 1},
                    display={**pointer, "format": format, "position": {"attempt": 1, "sequence": 1}},
                )
            )
        # The newer one is due first, so filtering after the claim's limit would find nothing to claim.
        await session.execute(
            update(RunRow).where(RunRow.id == newer).values(available_at=RunRow.created_at - timedelta(seconds=1))
        )

    (lease,) = await claim(service.runtime, worker_id="worker-test", worker_build="test", limit=1)
    assert lease.run_id == older
    await execute(service.runtime, lease, AttemptControl())
    failed = await runs_kit.get_run(service, older)
    assert failed["status"] == "failed" and failed["failure"]["code"] == "checkpoint_incompatible", failed
    assert (await runs_kit.get_run(service, newer))["status"] == "accepted"


async def test_a_successor_waits_for_a_worker_that_reads_its_parents_checkpoint(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    submitted = await runs_kit.start_thread(service, agent, "first")
    parent_id = submitted["run"]["id"]
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    second = await runs_kit.submit(service, submitted["thread"]["id"], runs_kit.message(agent, "second"))
    successor = second.json()["run"]
    assert successor["parent_run_id"] == parent_id, second.text

    async def rewrite_parent(checkpoint: dict) -> None:
        """As a newer worker would have committed it; a sealed run's checkpoint is otherwise immutable."""
        async with transaction(service.runtime.storage) as session:
            await session.execute(text("SET LOCAL session_replication_role = replica"))
            await session.execute(update(RunRow).where(RunRow.id == parent_id).values(checkpoint=checkpoint))

    # The successor has no checkpoint of its own yet: its parent's format decides who may claim it.
    async with transaction(service.runtime.storage) as session:
        committed = (await session.get_one(RunRow, parent_id)).checkpoint
    await rewrite_parent({**committed, "format": FORMAT + 1})
    assert await claim(service.runtime, worker_id="worker-test", worker_build="test", limit=1) == []
    await rewrite_parent(committed)
    (lease,) = await claim(service.runtime, worker_id="worker-test", worker_build="test", limit=1)
    assert lease.run_id == successor["id"]


async def test_a_cancelled_attempt_still_records_its_usage(service, scripted_model, runs_kit, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Inline child charges persist before the parent's next checkpoint and survive cancellation."""
    charged: set[str] = set()
    both = asyncio.Event()
    ingested = UsageBuffer.ingested_snapshot

    def watched(buffer: UsageBuffer, snapshot: UsageSnapshot) -> None:
        ingested(buffer, snapshot)
        charged.update(record.record_id for record in snapshot.records if isinstance(record, ModelUsageRecord))
        if len(charged) >= 2:
            both.set()

    monkeypatch.setattr(UsageBuffer, "ingested_snapshot", watched)
    worker = {"toolsets": {"configuration": {"enabled": True}}}
    agent = await runs_kit.delegating(service, scripted_model, "inline", worker=worker)
    gate = asyncio.Event()
    scripted_model.call(
        "delegate", {"subagent": "helper", "prompt": "compute"}, call_id="call_d", to="Role: coordinator"
    )
    scripted_model.call("find_resources", {"kind": "model"}, call_id="call_w", to="Role: worker")
    scripted_model.say("42", gate=gate, to="Role: worker")
    run_id = (await runs_kit.start_thread(service, agent, "ask the helper"))["run"]["id"]
    running = await runs_kit.attempt(service)
    async with asyncio.timeout(10):
        await both.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    gate.set()
    async with transaction(service.runtime.storage) as session:
        records = (
            await session.scalars(
                select(UsageRecordRow).where(
                    UsageRecordRow.run_id == run_id, UsageRecordRow.record["kind"].astext == "model"
                )
            )
        ).all()
    assert {record.record["record_id"] for record in records} == charged


async def test_a_stale_attempt_changes_nothing_after_a_takeover(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    # This test drives expiry and takeover; a concurrent sweep can make claim skip the locked run.
    await runs_kit.pause_sweeps(service)
    runtime = service.runtime
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    (stale,) = await claim(runtime, worker_id="worker-stale", worker_build="test", limit=1)
    async with transaction(runtime.storage) as session:
        await session.execute(
            update(AttemptRow).where(AttemptRow.run_id == run_id).values(lease_expires_at=AttemptRow.created_at)
        )
    await expire_leases(runtime, batch=10)
    async with transaction(runtime.storage) as session:
        await session.execute(update(RunRow).where(RunRow.id == run_id).values(available_at=RunRow.created_at))
    (current,) = await claim(runtime, worker_id="worker-current", worker_build="test", limit=1)

    with pytest.raises(LeaseLost):
        await seal_attempt(runtime, stale, Outcome.cancelled())
    both = [stale, current]
    stops, extended = await renew(
        runtime.storage, runtime.access, both, extend={lease.attempt_id for lease in both}, seconds=30
    )
    assert (stops, extended) == ({}, {current.attempt_id})
    run = await runs_kit.get_run(service, run_id)
    assert run["status"] == "running" and run["attempts"] == 2
    attempts = (await service.client.get(f"{service.api}/runs/{run_id}/attempts")).json()["items"]
    assert [(item["status"], item["start_reason"]) for item in attempts] == [
        ("failed", "initial"),
        ("leased", "recovery"),
    ]


@contextmanager
def _statements(runtime: Runtime) -> Iterator[list[str]]:
    """The SQL statements this task sends; transaction control and other tasks' work are not counted."""
    statements, owner = [], asyncio.current_task()

    def record(connection, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
        if asyncio.current_task() is owner:
            statements.append(statement)

    engine = runtime.storage.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def _expiries(runtime: Runtime) -> dict[str, datetime]:
    async with transaction(runtime.storage) as session:
        return dict((await session.execute(select(AttemptRow.id, AttemptRow.lease_expires_at))).tuples().all())


async def test_a_heartbeat_takes_the_same_statements_for_one_or_many_attempts(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    runtime = service.runtime
    agent = await runs_kit.create_agent(service, scripted_model)
    for _ in range(8):
        await runs_kit.start_thread(service, agent, "hi")
    leases = await claim(runtime, worker_id="worker-test", worker_build="test", limit=8)
    assert len(leases) == 8
    for batch in (leases[:1], leases):
        with _statements(runtime) as polled:
            assert await renew(runtime.storage, runtime.access, batch, extend=(), seconds=30) == ({}, set())
        # Runs, their workspaces, the principals and their grants.
        assert len(polled) == 4, polled
        due = {lease.attempt_id for lease in batch}
        with _statements(runtime) as extending:
            assert await renew(runtime.storage, runtime.access, batch, extend=due, seconds=30) == ({}, due)
        # Then one lock and one update for every due lease.
        assert len(extending) == 6, extending

    before = await _expiries(runtime)
    first = leases[0].attempt_id
    await renew(runtime.storage, runtime.access, leases, extend={first}, seconds=30)
    after = await _expiries(runtime)
    assert {key for key in before if after[key] != before[key]} == {first}


async def test_a_heartbeat_extends_only_leases_it_proves_and_never_waits(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    runtime = service.runtime
    agent = await runs_kit.create_agent(service, scripted_model)
    for _ in range(4):
        await runs_kit.start_thread(service, agent, "hi")
    healthy, expired, locked, forged = await claim(runtime, worker_id="worker-test", worker_build="test", limit=4)
    async with transaction(runtime.storage) as session:
        await session.execute(
            update(AttemptRow).where(AttemptRow.id == expired.attempt_id).values(lease_expires_at=AttemptRow.created_at)
        )
    forged = replace(forged, token="forged")
    leases = [healthy, expired, locked, forged]
    before = await _expiries(runtime)
    async with transaction(runtime.storage) as blocker:
        # An execution's own write holds its run, or an attempt row, until it commits.
        await blocker.execute(select(RunRow.id).where(RunRow.id == healthy.run_id).with_for_update())
        await blocker.execute(select(AttemptRow.id).where(AttemptRow.id == locked.attempt_id).with_for_update())
        async with asyncio.timeout(5):
            _, extended = await renew(
                runtime.storage, runtime.access, leases, extend={lease.attempt_id for lease in leases}, seconds=30
            )
            # With every due lease held elsewhere or unproven, nothing is extended.
            unproven = [locked, forged]
            _, blocked = await renew(
                runtime.storage, runtime.access, unproven, extend={lease.attempt_id for lease in unproven}, seconds=30
            )
    assert extended == {healthy.attempt_id} and blocked == set()
    after = await _expiries(runtime)
    assert {key for key in before if after[key] != before[key]} == {healthy.attempt_id}
    # The skipped lease is still due, and the next heartbeat extends it once its row is free.
    _, extended = await renew(runtime.storage, runtime.access, [locked], extend={locked.attempt_id}, seconds=30)
    assert extended == {locked.attempt_id}


async def test_a_heartbeat_stops_cancelled_and_revoked_runs_and_renews_nothing_without_authority(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    runtime = service.runtime
    agent = await runs_kit.create_agent(service, scripted_model)
    await runs_kit.start_thread(service, agent, "healthy")
    cancelled = (await runs_kit.start_thread(service, agent, "cancel me"))["run"]["id"]
    account = await service.client.post(f"{service.workspace}/service-accounts", json={"name": "bot"})
    assert account.status_code == 201, account.text
    bot = account.json()["id"]
    key = await service.client.post(f"{service.workspace}/service-accounts/{bot}/keys", json={"name": "key"})
    started = await service.client.post(
        f"{service.api}/threads",
        json=runs_kit.message(agent, "bot"),
        headers={"authorization": "Bearer " + key.json()["secret"], "idempotency-key": "bot-run"},
    )
    assert started.status_code == 201, started.text
    revoked = started.json()["run"]["id"]
    leases = {lease.run_id: lease for lease in await claim(runtime, worker_id="w", worker_build="test", limit=3)}
    async with transaction(runtime.storage) as session:
        await session.execute(
            update(RunRow).where(RunRow.id == cancelled).values(cancel_requested_at=func.clock_timestamp())
        )
        await session.execute(delete(GrantRow).where(GrantRow.principal_id == bot))
    due = {lease.attempt_id for lease in leases.values()}

    stops, extended = await renew(runtime.storage, runtime.access, list(leases.values()), extend=due, seconds=30)
    assert stops == {
        leases[cancelled].attempt_id: Outcome.cancelled(),
        leases[revoked].attempt_id: AuthorityRevoked().outcome,
    }
    # A stopping run still renews: it seals its outcome under the lease.
    assert extended == due

    class Unavailable:
        async def grants_for(self, principal: Principal) -> Sequence[RoleGrant]:
            raise ServiceError("unavailable", "Directory cache is stale")

    before = await _expiries(runtime)
    with pytest.raises(ServiceError, match="stale"):
        await renew(
            runtime.storage,
            replace(runtime.access, sources=(Unavailable(),)),
            list(leases.values()),
            extend=due,
            seconds=30,
        )
    assert await _expiries(runtime) == before


async def test_the_lease_expiry_sweep_passes_a_run_it_cannot_recover(
    service, scripted_model, runs_kit, monkeypatch, caplog
) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    failing, recovered = [(await runs_kit.start_thread(service, agent, text))["run"]["id"] for text in ("one", "two")]
    assert len(await claim(service.runtime, worker_id="worker-test", worker_build="test", limit=2)) == 2
    async with transaction(service.runtime.storage) as session:
        for run_id, expired in ((failing, timedelta(minutes=2)), (recovered, timedelta(minutes=1))):
            await session.execute(
                update(AttemptRow)
                .where(AttemptRow.run_id == run_id)
                .values(lease_expires_at=AttemptRow.created_at - expired)
            )
    recover = seal_module.recover

    async def recover_all_but_one(session, runtime, thread, run, attempt, **fields) -> None:  # type: ignore[no-untyped-def]
        if run.id == failing:
            raise RuntimeError("cannot recover")
        await recover(session, runtime, thread, run, attempt, **fields)

    monkeypatch.setattr(seal_module, "recover", recover_all_but_one)
    # The sweep visits the longest-expired lease first.
    await expire_leases(service.runtime, batch=10)
    assert (await runs_kit.get_run(service, failing))["status"] == "running"
    assert (await runs_kit.get_run(service, recovered))["status"] == "accepted"
    [failure] = [record for record in caplog.records if record.getMessage() == "Lease expiry failed"]
    assert failure.run_id == failing
    assert failure.exception_details[0]["frames"][-1]["function"] == "recover_all_but_one"
    assert "cannot recover" not in json.dumps(vars(failure))


class _UndeletableObjects:
    """An object store whose deletions fail, as they do while the store is briefly unavailable."""

    def __init__(self, objects) -> None:  # type: ignore[no-untyped-def]
        self.objects = objects

    async def put(self, key: str, data: bytes, *, content_type: str):  # type: ignore[no-untyped-def]
        return await self.objects.put(key, data, content_type=content_type)

    async def get(self, key: str) -> bytes | None:
        return await self.objects.get(key)

    async def keys(self, prefix: str, *, limit: int, after: str | None = None) -> list[str]:
        return await self.objects.keys(prefix, limit=limit, after=after)

    async def delete(self, key: str) -> None:
        raise ServiceError("unavailable", "Object store unavailable")


async def test_a_failed_cleanup_of_replaced_objects_costs_no_attempt(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    scripted_model.say("Done")
    runtime = replace(service.runtime, objects=_UndeletableObjects(service.runtime.objects))
    await (await runs_kit.attempt(service, runtime=runtime))
    run = await runs_kit.get_run(service, run_id)
    assert run["status"] == "completed" and run["attempts"] == 1, run


async def test_a_usage_record_reported_again_with_other_content_is_skipped(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    async with transaction(service.runtime.storage) as session:
        stored = (
            await session.scalars(
                select(UsageRecordRow).where(
                    UsageRecordRow.run_id == run_id, UsageRecordRow.record["kind"].astext == "model"
                )
            )
        ).one()
        digest, model_id = stored.digest, stored.model_id

    # The same record ID now claims no model: the stored fact stays, and reporting it again does not fail.
    conflicting = UsageReport(ModelUsageRecord.model_validate(stored.record))
    await ingest_late(service.runtime.storage, run_id, stored.run_attempt_id, [conflicting])
    async with transaction(service.runtime.storage) as session:
        kept = (
            await session.scalars(
                select(UsageRecordRow).where(
                    UsageRecordRow.run_id == run_id, UsageRecordRow.record["kind"].astext == "model"
                )
            )
        ).one()
    assert (kept.digest, kept.model_id) == (digest, model_id) and model_id is not None


async def _released(service, run_id: str) -> list[dict]:  # type: ignore[no-untyped-def]
    """The run's attempts, after its only attempt ended without sealing it."""
    run = await service.client.get(f"{service.api}/runs/{run_id}")
    assert run.json()["status"] == "accepted", run.text
    return (await service.client.get(f"{service.api}/runs/{run_id}/attempts")).json()["items"]


async def test_an_unavailable_dependency_ends_a_tool_calls_attempt(
    service, scripted_model, runs_kit, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """An outage is not the tool call's failure for the model to read: the attempt ends, and a later one retries."""
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model, toolsets={"configuration": {"enabled": True}})
    scripted_model.call("find_resources", {"kind": "model"}, call_id="call_find")
    run_id = (await runs_kit.start_thread(service, agent, "look around"))["run"]["id"]

    async def unavailable(*args: object, **kwargs: object) -> None:
        raise ServiceError("unavailable", "The database is unavailable")

    monkeypatch.setattr(models_service, "list_models", unavailable)
    await (await runs_kit.attempt(service))

    attempts = await _released(service, run_id)
    assert [(item["status"], item["failure"]["code"]) for item in attempts] == [("failed", "attempt_failed")]
    await scripted_model.request()
    assert scripted_model.requests.empty()


async def test_url_input_that_cannot_be_reached_is_fetched_by_a_later_attempt(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    offline = {"agent_id": agent["id"], "payload": {"content": [{"type": "url", "url": "http://127.0.0.1:9/page"}]}}
    response = await service.client.post(f"{service.api}/threads", json=offline, headers=runs_kit.fresh_key())
    assert response.status_code == 201, response.text
    run_id = response.json()["run"]["id"]
    await (await runs_kit.attempt(service))

    # A network error is transient: the entry stays with its run for the next attempt, which fetches it again.
    attempts = await _released(service, run_id)
    assert [(item["status"], item["failure"]["code"]) for item in attempts] == [("failed", "attempt_failed")]
    (entry,) = await runs_kit.inbox(service, response.json()["thread"]["id"])
    assert (entry["status"], entry["assigned_run_id"]) == ("assigned", run_id), entry
    assert scripted_model.requests.empty()


async def test_an_outcome_commits_only_with_its_seal(service, scripted_model, runs_kit, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The final checkpoint and the seal are one transaction: when the seal fails neither lands, and a later attempt
    continues from the checkpoint before the model request that produced the outcome."""
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    sealing = execute_module.seal

    async def failing(*args: object, **kwargs: object) -> None:
        raise RuntimeError("The seal failed")

    monkeypatch.setattr(execute_module, "seal", failing)
    scripted_model.say("Lost")
    await (await runs_kit.attempt(service))
    attempts = await _released(service, run_id)
    assert [(item["status"], item["failure"]["code"]) for item in attempts] == [("failed", "attempt_failed")]
    async with transaction(service.runtime.storage) as session:
        run = await session.get_one(RunRow, run_id)
        state = await checkpoints.load_state(
            service.runtime.objects,
            run.organization_id,
            run.id,
            checkpoints.StatePointer.model_validate(run.checkpoint),
        )
        assert state is not None and state.seq == 1
        await session.execute(update(RunRow).where(RunRow.id == run_id).values(available_at=RunRow.created_at))

    monkeypatch.setattr(execute_module, "seal", sealing)
    scripted_model.say("Sealed")
    await (await runs_kit.attempt(service))
    sealed = await runs_kit.get_run(service, run_id)
    assert sealed["status"] == "completed" and sealed["output"] == "Sealed" and sealed["attempts"] == 2, sealed


@pytest.mark.parametrize("question, failed", [(False, False), (True, False), (False, True)])
async def test_takeover_keeps_external_answer_without_replaying_local_approval(
    service, scripted_model, runs_kit, monkeypatch, question, failed
) -> None:  # type: ignore[no-untyped-def]
    import json

    from a13n_service.runs.boundaries import Boundaries

    await runs_kit.pause_sweeps(service)
    model = await runs_kit.create_model(service, scripted_model)
    agent = await runs_kit.add_agent(
        service,
        "mixed",
        model,
        user_questions=question,
        client_tools=[{"name": "lookup", "description": "External fact", "parameters_json_schema": {"type": "object"}}],
        toolsets={"configuration": {"enabled": True}},
    )
    calls = [
        ("create_agent", {"name": "Created", "config": {"model": model}}, "call_create"),
        ("ask_user_question", {"questions": [runs_kit.QUESTION]}, "call_lookup")
        if question
        else ("lookup", {}, "call_lookup"),
    ]
    scripted_model._script(
        {
            "deltas": [
                {
                    "tool_calls": [
                        {
                            "index": index,
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                        for index, (name, args, call_id) in enumerate(calls)
                    ]
                }
            ],
            "interval": 0,
            "finish": "tool_calls",
            "gate": None,
            "to": None,
        }
    )
    first = await runs_kit.start_thread(service, agent, "change and look up")
    await (await runs_kit.attempt(service))
    waiting = await runs_kit.get_run(service, first["run"]["id"])
    assert waiting["status"] == "waiting", waiting
    response = await service.client.post(
        f"{service.api}/runs/{waiting['id']}/resume",
        json={
            "approvals": {"call_create": {"action": "approve"}},
            "calls": {
                "call_lookup": (
                    {"status": "failed", "message": "accepted once"}
                    if failed
                    else {"status": "returned", "value": {"response" if question else "fact": "accepted once"}}
                )
            },
        },
        headers=runs_kit.fresh_key(),
    )
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    committed = asyncio.Event()
    before = Boundaries.before_tool_execute

    async def stop_after_commit(self, ctx, *, call, tool_def, args):
        args = await before(self, ctx, call=call, tool_def=tool_def, args=args)
        if call.tool_call_id == "call_create":
            committed.set()
            await asyncio.Event().wait()
        return args

    monkeypatch.setattr(Boundaries, "before_tool_execute", stop_after_commit)
    running = await runs_kit.attempt(service)
    try:
        async with asyncio.timeout(10):
            await committed.wait()
    finally:
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    monkeypatch.setattr(Boundaries, "before_tool_execute", before)
    async with transaction(service.runtime.storage) as session:
        run = await session.get_one(RunRow, run_id)
        assert run.checkpoint is not None and run.resume is not None
        await session.execute(
            update(AttemptRow).where(AttemptRow.run_id == run_id).values(lease_expires_at=AttemptRow.created_at)
        )
    await expire_leases(service.runtime, batch=10)
    async with transaction(service.runtime.storage) as session:
        await session.execute(update(RunRow).where(RunRow.id == run_id).values(available_at=RunRow.created_at))
    scripted_model.say("Recovered the known fact")
    await (await runs_kit.attempt(service))
    result = await runs_kit.get_run(service, run_id)
    assert result["status"] == "completed" and result["attempts"] == 2, result
    agents = (await service.client.get(f"{service.api}/agents", params={"source": "custom"})).json()["items"]
    assert [item["name"] for item in agents] == ["Mixed"]
    await scripted_model.request()
    resumed = await scripted_model.request()
    returns = [message for message in resumed["messages"] if message["role"] == "tool"]
    external = [message for message in returns if message["tool_call_id"] == "call_lookup"]
    assert len(external) == 1 and "accepted once" in external[0]["content"]
    assert len([message for message in returns if message["tool_call_id"] == "call_create"]) == 1


async def test_slow_object_deletion_does_not_block_tool_boundaries_or_successor_acceptance(
    service, scripted_model, runs_kit, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model, toolsets={"configuration": {"enabled": True}})
    gate, deleting, finish_delete = asyncio.Event(), asyncio.Event(), asyncio.Event()
    scripted_model.call("find_resources", {"kind": "model"}, call_id="call_find", gate=gate)
    scripted_model.say("Done")
    submitted = await runs_kit.start_thread(service, agent, "look around")
    run_id, thread_id = submitted["run"]["id"], submitted["thread"]["id"]
    original_delete = service.runtime.objects.delete

    async def slow_delete(key: str) -> None:
        deleting.set()
        await finish_delete.wait()
        await original_delete(key)

    monkeypatch.setattr(service.runtime.objects, "delete", slow_delete)
    from functools import partial

    from a13n_service.infra.db import short_session
    from a13n_service.infra.outbox import Delivery, OutboxRow
    from a13n_service.runs.checkpoints import CLEANUP, clean

    running = await runs_kit.attempt(service)

    async def reclaim() -> None:
        # The first boundary must be committed before its reclamation intent is visible.
        async with asyncio.timeout(4):
            while True:
                async with short_session(service.runtime.storage) as session:
                    if await session.scalar(select(OutboxRow.id).where(OutboxRow.kind == CLEANUP)):
                        break
                await asyncio.sleep(0.01)
        await Delivery(
            service.runtime.storage,
            {CLEANUP: partial(clean, service.runtime)},
            owner="test",
            policies=service.runtime.settings.outbox.policies,
        )()

    cleanup = asyncio.create_task(reclaim())
    try:
        await scripted_model.request()
        next_entry = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "next", delivery="next_run"))
        gate.set()
        async with asyncio.timeout(4):
            await deleting.wait()
            await running
        assert not finish_delete.is_set()
        run = await runs_kit.get_run(service, run_id)
        assert run["status"] == "completed"
        entries = {entry["id"]: entry for entry in await runs_kit.inbox(service, thread_id)}
        assert entries[next_entry.json()["entry"]["id"]]["status"] == "assigned"
    finally:
        gate.set()
        finish_delete.set()
        await asyncio.gather(running, return_exceptions=True)
        await cleanup


async def test_mount_handoff_returns_while_process_keeps_the_cancelled_child_owned(
    service, scripted_model, runs_kit, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.runs.schemas import EnvironmentMount

    agent = await runs_kit.create_agent(service, scripted_model)
    await runs_kit.start_thread(service, agent, "hi")
    (lease,) = await claim(service.runtime, worker_id="worker-test", worker_build="test", limit=1)
    plan = await execute_module._plan(service.runtime, lease)
    plan = replace(
        plan,
        mounts=(EnvironmentMount(name="workspace", environment_id="env_test"),),
    )
    control = AttemptControl(deadline=asyncio.get_running_loop().time() + 60, renewal_margin=10)
    started, cleaning, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def prepare(*args) -> list:  # type: ignore[no-untyped-def]
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await finish.wait()

    monkeypatch.setattr(execute_module, "prepare_mounts", prepare)
    attempt = execute_module._Attempt(service.runtime, lease, control, plan)
    waiting = asyncio.create_task(attempt._prepare_mounts())
    try:
        await started.wait()
        control.handoff.set()
        assert await waiting is None
        await cleaning.wait()
        assert any(task.get_name() == f"prepare-mounts-{lease.attempt_id}" for task in service.runtime.tasks.pending)
    finally:
        finish.set()
        await asyncio.gather(waiting, return_exceptions=True)
        await service.runtime.tasks.close(timeout=5)
