"""Latest accounting stays durable independently of fenced execution checkpoints."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from a13n_harness import StateError
from a13n_harness.usage import BoundedRequestUsage, ModelUsageRecord, UsageSnapshot
from a13n_service.infra.db import transaction
from a13n_service.runs.claim import claim
from a13n_service.runs.tables import RunRow, UsageRecordRow
from a13n_service.runs.usage import SnapshotReporter, UsageBuffer, ingest_snapshot, totals
from sqlalchemy import select

pytestmark = pytest.mark.anyio


def snapshot(owner: str, tokens: int, sequence: int) -> UsageSnapshot:
    return UsageSnapshot(
        usage_id=f"scope_{owner}",
        run_id=owner,
        agent_instance_id="root",
        sequence=sequence,
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


async def test_latest_snapshot_replaces_projection_without_revising_execution(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
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
    first, latest = snapshot("late", 4, 1), snapshot("late", 9, 2)
    await ingest_snapshot(service.runtime.storage, run_id, attempt_id, first, {})
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(UsageRecordRow, "record_late")
        ingested_at = row.ingested_at
    await asyncio.gather(
        *(ingest_snapshot(service.runtime.storage, run_id, attempt_id, item, {}) for item in (latest, first, latest))
    )
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(UsageRecordRow, "record_late")
        scope = await session.get_one(UsageRecordRow, "scope_late")
        run = await session.get_one(RunRow, run_id)
        assert row.record["request_usage"]["input_tokens"] == 9 and row.ingested_at == ingested_at
        assert scope.record == latest.model_dump(mode="json")
        assert (run.checkpoint, run.usage_at_seal) == (checkpoint, sealed)
        assert (await totals(session, run_id))["requests"] == 2
    with pytest.raises(StateError, match="conflicting facts"):
        await ingest_snapshot(service.runtime.storage, run_id, attempt_id, snapshot("late", 10, 2), {})


async def test_reporter_retains_failed_delivery_for_retry(service, scripted_model, runs_kit, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.runs import usage

    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    [lease] = await claim(service.runtime, worker_id="test", worker_build="test", limit=1)
    reporter = SnapshotReporter(service.runtime.storage, run_id, lease.attempt_id, UsageBuffer({}))
    original = usage.ingest_snapshot

    async def unavailable(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("database unavailable")

    monkeypatch.setattr(usage, "ingest_snapshot", unavailable)
    with pytest.raises(OSError):
        await reporter.report(snapshot("retry", 4, 1))
    monkeypatch.setattr(usage, "ingest_snapshot", original)
    await reporter.flush()
    async with transaction(service.runtime.storage) as session:
        assert (await session.get_one(UsageRecordRow, "scope_retry")).record["sequence"] == 1
        assert (await totals(session, run_id))["requests"] == 1


async def test_upgrade_preserves_legacy_facts_and_accepts_current_scopes(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.distribution import OSS
    from a13n_service.migrations.runner import migration_connection, upgrade
    from a13n_service.runs.usage import UsageReport, ingest_late
    from alembic import command
    from sqlalchemy import update
    from sqlalchemy.exc import DBAPIError

    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    [lease] = await claim(service.runtime, worker_id="test", worker_build="test", limit=1)

    def previous_schema() -> None:
        with migration_connection(service.runtime.settings.database, OSS) as config:
            command.downgrade(config, "295f267e708c")

    await asyncio.to_thread(previous_schema)
    legacy = snapshot("legacy", 5, 1).records[0]
    await ingest_late(service.runtime.storage, run_id, lease.attempt_id, [UsageReport(legacy)])
    async with transaction(service.runtime.storage) as session:
        before = await session.get_one(UsageRecordRow, legacy.record_id)
    await asyncio.to_thread(upgrade, service.runtime.settings.database, OSS)
    await ingest_snapshot(service.runtime.storage, run_id, lease.attempt_id, snapshot("new", 3, 1), {})
    async with transaction(service.runtime.storage) as session:
        after = await session.get_one(UsageRecordRow, legacy.record_id)
        assert (after.record, after.digest, after.ingested_at) == (before.record, before.digest, before.ingested_at)
        assert (await totals(session, run_id))["input_tokens"] == 8
    with pytest.raises(DBAPIError, match="immutable"):
        async with transaction(service.runtime.storage) as session:
            await session.execute(
                update(UsageRecordRow).where(UsageRecordRow.id == legacy.record_id).values(digest="0" * 64)
            )


async def test_takeover_keeps_late_old_scope_and_deduplicates_provider_receipts(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    from a13n_harness.usage import ProviderUsage, ProviderUsageRecord
    from a13n_service.runs.seal import expire_leases
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
    await expire_leases(service.runtime, batch=10)
    async with transaction(service.runtime.storage) as session:
        await session.execute(update(RunRow).where(RunRow.id == run_id).values(available_at=RunRow.created_at))
    [new] = await claim(service.runtime, worker_id="new", worker_build="test", limit=1)

    def with_receipt(owner: str) -> UsageSnapshot:
        value = snapshot(owner, 3, 1)
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

    await ingest_snapshot(service.runtime.storage, run_id, old.attempt_id, with_receipt("old"), {})
    await ingest_snapshot(service.runtime.storage, run_id, new.attempt_id, with_receipt("new"), {})
    late = with_receipt("old").model_copy(update={"sequence": 2, "tool_calls": 1})
    await ingest_snapshot(service.runtime.storage, run_id, old.attempt_id, late, {})
    async with transaction(service.runtime.storage) as session:
        receipt = await session.get_one(UsageRecordRow, "receipt_shared")
        assert receipt.run_attempt_id == old.attempt_id
        assert (await totals(session, run_id))["requests"] == 2
        assert (await session.get_one(UsageRecordRow, "scope_old")).record["tool_calls"] == 1
        assert (await session.get_one(UsageRecordRow, "scope_new")).record["sequence"] == 1


@pytest.mark.parametrize("limit", [2, 3])
async def test_request_budget_counts_inline_calls_without_counting_snapshot_delivery(
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
