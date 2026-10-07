"""Latest accounting stays durable independently of fenced execution checkpoints."""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from a13n_harness.usage import BoundedRequestUsage, ModelUsageRecord, UsageDelta, UsageScope
from a13n_service.infra.db import transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.claim import claim
from a13n_service.runs.tables import RunRow, UsageRecordRow
from a13n_service.runs.usage import DeltaReporter, UsageBuffer, ingest_delta, totals
from sqlalchemy import event, select

pytestmark = pytest.mark.anyio


def delta(owner: str, tokens: int, sequence: int) -> UsageDelta:
    return UsageDelta(
        scope=UsageScope(usage_id=f"scope_{owner}", run_id=owner, agent_instance_id="root", sequence=sequence),
        after_sequence=sequence - 1,
        records=(
            ModelUsageRecord(
                record_id=f"record_{owner}",
                run_id=owner,
                agent_instance_id="root",
                response_ordinal=1,
                response_state="complete",
                response_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                request_usage=BoundedRequestUsage(input_tokens=tokens),
            ),
        ),
    )


async def test_latest_delta_replaces_contribution_without_revising_execution(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    async with transaction(service.runtime.storage) as session:
        run = await session.get_one(RunRow, run_id)
        checkpoint, sealed = run.checkpoint, run.usage_at_seal
        attempt_id = (
            await session.scalars(select(UsageRecordRow.run_attempt_id).where(UsageRecordRow.run_id == run_id))
        ).first()
    assert attempt_id is not None
    first, latest = delta("late", 4, 1), delta("late", 9, 2)
    await ingest_delta(service.runtime.storage, run_id, attempt_id, first, {})
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(UsageRecordRow, "record_late")
        ingested_at = row.ingested_at
    await asyncio.gather(
        *(ingest_delta(service.runtime.storage, run_id, attempt_id, item, {}) for item in (latest, first, latest))
    )
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(UsageRecordRow, "record_late")
        scope = await session.get_one(UsageRecordRow, "scope_late")
        run = await session.get_one(RunRow, run_id)
        assert row.record["request_usage"]["input_tokens"] == 9 and row.ingested_at == ingested_at
        assert scope.record == {"kind": "cursor", **latest.scope.model_dump(mode="json")}
        assert (run.checkpoint, run.usage_at_seal) == (checkpoint, sealed)
        assert (await totals(session, run_id))["requests"] == 2
    with pytest.raises(ServiceError, match="conflicting facts"):
        await ingest_delta(service.runtime.storage, run_id, attempt_id, delta("late", 10, 2), {})


async def test_reporter_retains_failed_delivery_for_retry(service, scripted_model, runs_kit, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.runs import usage

    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    [lease] = await claim(service.runtime, worker_id="test", worker_build="test", limit=1)
    reporter = DeltaReporter(service.runtime.storage, run_id, lease.attempt_id, UsageBuffer({}))
    original = usage.ingest_delta

    async def unavailable(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("database unavailable")

    monkeypatch.setattr(usage, "ingest_delta", unavailable)
    with pytest.raises(OSError):
        await reporter.report_delta(delta("retry", 4, 1))
    monkeypatch.setattr(usage, "ingest_delta", original)
    await reporter.flush()
    async with transaction(service.runtime.storage) as session:
        assert (await session.get_one(UsageRecordRow, "scope_retry")).record["sequence"] == 1
        assert (await totals(session, run_id))["requests"] == 1


async def test_unknown_commit_outcome_retries_without_recounting(service, scripted_model, runs_kit, monkeypatch):
    from a13n_service.runs import usage

    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    [lease] = await claim(service.runtime, worker_id="test", worker_build="test", limit=1)
    reporter = DeltaReporter(service.runtime.storage, run_id, lease.attempt_id, UsageBuffer({}))
    original = usage.ingest_delta

    async def committed_without_ack(*args, **kwargs):
        await original(*args, **kwargs)
        raise OSError("commit acknowledgement lost")

    monkeypatch.setattr(usage, "ingest_delta", committed_without_ack)
    with pytest.raises(OSError):
        await reporter.report_delta(delta("retry", 4, 1))
    monkeypatch.setattr(usage, "ingest_delta", original)
    # A subsequent report can still include the unacknowledged earlier interval.
    await reporter.report_delta(delta("retry", 9, 2).model_copy(update={"after_sequence": 0}))
    async with transaction(service.runtime.storage) as session:
        assert (await totals(session, run_id)) == {"requests": 1, "input_tokens": 9, "output_tokens": 0}
        assert (await session.get_one(UsageRecordRow, "scope_retry")).record["sequence"] == 2


async def test_gap_and_conflicting_contribution_do_not_advance_progress(service, scripted_model, runs_kit):
    from a13n_harness import RunError

    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    [lease] = await claim(service.runtime, worker_id="test", worker_build="test", limit=1)
    storage = service.runtime.storage
    first = delta("atomic", 4, 1)
    with pytest.raises(ServiceError, match="skipped"):
        await ingest_delta(storage, run_id, lease.attempt_id, delta("atomic", 9, 2), {})
    await ingest_delta(storage, run_id, lease.attempt_id, first, {})
    with pytest.raises(ServiceError, match="skipped"):
        await ingest_delta(storage, run_id, lease.attempt_id, delta("atomic", 9, 3), {})
    second = delta("atomic", 9, 2)
    invalid = second.model_copy(
        update={
            "records": (
                second.records[0].model_copy(update={"source": "changed"}),
                second.records[0].model_copy(update={"record_id": "new-record"}),
            )
        }
    )
    with pytest.raises(RunError, match="attribution"):
        await ingest_delta(storage, run_id, lease.attempt_id, invalid, {})
    async with transaction(storage) as session:
        assert (await session.get_one(UsageRecordRow, "scope_atomic")).record["sequence"] == 1
        assert await session.get(UsageRecordRow, "new-record") is None
        assert (await totals(session, run_id))["input_tokens"] == 4
    await ingest_delta(storage, run_id, lease.attempt_id, second, {})


@pytest.mark.parametrize("refinement", [False, True])
async def test_database_work_depends_on_changes_not_scope_size(service, scripted_model, runs_kit, refinement):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    [lease] = await claim(service.runtime, worker_id="test", worker_build="test", limit=1)
    storage = service.runtime.storage
    measurements = []
    for size in (100, 1000, 9000):
        initial = delta(f"scale-{size}", 4, size)
        records = tuple(initial.records[0].model_copy(update={"record_id": f"record-{size}-{i}"}) for i in range(size))
        initial = initial.model_copy(update={"after_sequence": 0, "records": records})
        await ingest_delta(storage, run_id, lease.attempt_id, initial, {})
        changed = records[-1].model_copy(
            update={
                "record_id": records[-1].record_id if refinement else f"record-{size}-new",
                "request_usage": BoundedRequestUsage(input_tokens=7),
            }
        )
        current = UsageDelta(
            scope=initial.scope.model_copy(update={"sequence": size + 1}), after_sequence=size, records=(changed,)
        )
        statements = []
        owner = asyncio.current_task()

        def measured(
            connection, cursor, statement, parameters, context, executemany, *, owner=owner, statements=statements
        ):
            if asyncio.current_task() is owner:
                statements.append(
                    (statement, cursor.rowcount, len(json.dumps(context.compiled_parameters, default=str)))
                )

        engine = storage.engine.sync_engine
        event.listen(engine, "after_cursor_execute", measured)
        try:
            await ingest_delta(storage, run_id, lease.attempt_id, current, {})
        finally:
            event.remove(engine, "after_cursor_execute", measured)
        measurements.append((len(statements), sum(max(0, rows) for _, rows, _ in statements)))
        assert sum(bytes_ for _, _, bytes_ in statements) < 5000
        async with transaction(storage) as session:
            cursor = await session.get_one(UsageRecordRow, current.scope.usage_id)
            assert "records" not in cursor.record and len(json.dumps(cursor.record)) < 1024
            assert (await session.get_one(UsageRecordRow, changed.record_id)).record["request_usage"][
                "input_tokens"
            ] == 7
    assert len(set(measurements)) == 1
    assert measurements[0][0] <= 8 and measurements[0][1] <= 8


async def test_individual_facts_remain_immutable_alongside_current_scopes(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.runs.usage import UsageReport, ingest_late
    from sqlalchemy import update
    from sqlalchemy.exc import DBAPIError

    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    [lease] = await claim(service.runtime, worker_id="test", worker_build="test", limit=1)

    legacy = delta("legacy", 5, 1).records[0]
    await ingest_late(service.runtime.storage, run_id, lease.attempt_id, [UsageReport(legacy)])
    async with transaction(service.runtime.storage) as session:
        before = await session.get_one(UsageRecordRow, legacy.record_id)
    await ingest_delta(service.runtime.storage, run_id, lease.attempt_id, delta("new", 3, 1), {})
    async with transaction(service.runtime.storage) as session:
        after = await session.get_one(UsageRecordRow, legacy.record_id)
        assert (after.record, after.digest, after.ingested_at) == (before.record, before.digest, before.ingested_at)
        assert (await totals(session, run_id))["input_tokens"] == 8
    with pytest.raises(DBAPIError, match="immutable"):
        async with transaction(service.runtime.storage) as session:
            await session.execute(
                update(UsageRecordRow).where(UsageRecordRow.id == legacy.record_id).values(digest="0" * 64)
            )


async def test_old_snapshot_scopes_and_their_contributions_are_frozen(service, scripted_model, runs_kit):
    from a13n_harness.usage import UsageSnapshot
    from a13n_service.runs.usage import UsageReport, ingest_late
    from sqlalchemy.exc import DBAPIError

    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    [lease] = await claim(service.runtime, worker_id="test", worker_build="test", limit=1)
    storage = service.runtime.storage
    first, latest = delta("old-worker", 4, 1), delta("old-worker", 9, 2)
    await ingest_late(storage, run_id, lease.attempt_id, [UsageReport(first.records[0])])
    async with transaction(storage) as session:
        record = await session.get_one(UsageRecordRow, first.records[0].record_id)
        snapshot = UsageSnapshot(**first.scope.model_dump(), records=first.records)
        session.add(
            UsageRecordRow(
                id=snapshot.usage_id,
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                run_id=run_id,
                run_attempt_id=lease.attempt_id,
                harness_run_id=snapshot.run_id,
                record=snapshot.model_dump(mode="json"),
                digest="1" * 64,
            )
        )
    for row_id, replacement in (
        (first.scope.usage_id, UsageSnapshot(**latest.scope.model_dump(), records=latest.records)),
        (first.records[0].record_id, latest.records[0]),
    ):
        with pytest.raises(DBAPIError, match="immutable"):
            async with transaction(storage) as session:
                row = await session.get_one(UsageRecordRow, row_id)
                row.record = replacement.model_dump(mode="json")
                row.digest = "2" * 64
    # Historical contributions still count, but neither their scope nor their
    # values can be advanced by an old snapshot writer after the upgrade.
    async with transaction(storage) as session:
        assert (await totals(session, run_id))["input_tokens"] == 4
        assert (await session.get_one(UsageRecordRow, first.scope.usage_id)).record["sequence"] == 1
    with pytest.raises(ServiceError, match="reporting contract"):
        await ingest_delta(storage, run_id, lease.attempt_id, latest, {})


async def test_takeover_keeps_late_old_scope_and_deduplicates_provider_receipts(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    from a13n_harness.usage import ProviderUsage, ProviderUsageRecord
    from a13n_service.runs.seal import LeaseExpirer
    from a13n_service.runs.tables import AttemptRow
    from sqlalchemy import update

    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    [old] = await claim(service.runtime, worker_id="old", worker_build="test", limit=1)
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(AttemptRow).where(AttemptRow.id == old.attempt_id).values(lease_expires_at=AttemptRow.created_at)
        )
    await LeaseExpirer(service.runtime, batch=10)()
    async with transaction(service.runtime.storage) as session:
        await session.execute(update(RunRow).where(RunRow.id == run_id).values(available_at=RunRow.created_at))
    [new] = await claim(service.runtime, worker_id="new", worker_build="test", limit=1)

    def with_receipt(owner: str) -> UsageDelta:
        value = delta(owner, 3, 1)
        receipt = ProviderUsageRecord(
            record_id="receipt_shared",
            run_id=owner,
            agent_instance_id="root",
            ordinal=1,
            source="web",
            usage=ProviderUsage(
                provider="test",
                product="search",
                usage_id="shared",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                cost=Decimal("0.1"),
                currency="USD",
            ),
        )
        return value.model_copy(update={"records": (*value.records, receipt)})

    await ingest_delta(service.runtime.storage, run_id, old.attempt_id, with_receipt("old"), {})
    await ingest_delta(service.runtime.storage, run_id, new.attempt_id, with_receipt("new"), {})
    late = with_receipt("old")
    late = late.model_copy(
        update={
            "scope": late.scope.model_copy(update={"sequence": 2, "tool_calls": 1}),
            "after_sequence": 1,
            "records": (),
        }
    )
    await ingest_delta(service.runtime.storage, run_id, old.attempt_id, late, {})
    async with transaction(service.runtime.storage) as session:
        receipt = await session.get_one(UsageRecordRow, "receipt_shared")
        assert receipt.run_attempt_id == old.attempt_id
        assert (await totals(session, run_id))["requests"] == 2
        assert (await session.get_one(UsageRecordRow, "scope_old")).record["tool_calls"] == 1
        assert (await session.get_one(UsageRecordRow, "scope_new")).record["sequence"] == 1


@pytest.mark.parametrize("limit", [2, 3])
async def test_request_budget_counts_inline_calls_without_counting_delta_delivery(
    executing, scripted_model, runs_kit, limit
) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.delegating(executing, scripted_model, "inline")
    scripted_model.call("delegate", {"subagent": "helper", "prompt": "compute"}, call_id="call_d", to="coordinator")
    scripted_model.say("42", to="worker")
    scripted_model.say("The helper said 42", to="coordinator")
    response = await executing.client.post(
        f"{executing.api}/threads",
        json=runs_kit.message(agent, "compute", options={"max_usage": {"requests": limit}}),
        headers=runs_kit.fresh_key(),
    )
    assert response.status_code == 201, response.text
    run_id = response.json()["run"]["id"]
    sealed = await runs_kit.sealed(executing, run_id)
    assert sealed["status"] == ("completed" if limit == 3 else "failed")
    if limit == 2:
        assert sealed["failure"]["code"] == "usage_limit_exceeded"
    async with transaction(executing.runtime.storage) as session:
        assert (await totals(session, run_id))["requests"] == limit
