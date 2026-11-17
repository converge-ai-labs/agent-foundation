"""The shipped dashboard SQL computes exact durable values, not merely valid queries."""

from datetime import timedelta
from decimal import Decimal

import pytest
from a13n_service.infra.db import short_session, transaction
from a13n_service.resources.agents.tables import AgentRow
from a13n_service.runs.tables import UsageRecordRow
from a13n_service.tenancy.tables import WorkspaceRow
from sqlalchemy import select, text

from .monitoring_support import expand_usage, usage_queries
from .test_usage_analytics import START, observed_run

pytestmark = pytest.mark.anyio


async def test_dashboard_populations_prices_boundaries_and_identity(service, scripted_model, runs_kit):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    second = await runs_kit.add_agent(service, "helper", "scripted")
    first_run, _ = await observed_run(
        service,
        runs_kit,
        agent,
        records=[
            ("scripted", 100, 20, 90, Decimal("0.1"), START),
            ("scripted", 900, 30, 90, None, START + timedelta(days=1)),
            (None, 50, 10, 0, None, START + timedelta(days=2)),
            ("scripted", 9999, 0, 0, Decimal("99"), START + timedelta(days=3)),
        ],
    )
    await observed_run(service, runs_kit, second, seconds=30)
    await observed_run(
        service,
        runs_kit,
        agent,
        started=START - timedelta(days=1),
        records=[
            ("scripted", 100, 5, 50, Decimal("0.2"), START),
        ],
    )
    # Exact right-boundary Run is excluded, as is its right-boundary usage above.
    await observed_run(service, runs_kit, agent, started=START + timedelta(days=3))
    original_workspace = service.tenant.workspace_id
    other = await service.client.post(f"{service.organization}/workspaces", json={"name": "Other"})
    assert other.status_code == 201, other.text
    other_id = other.json()["id"]
    service.client.headers["x-workspace-id"] = other_id
    try:
        other_agent = await runs_kit.create_agent(service, scripted_model)
        await observed_run(service, runs_kit, other_agent, records=[("scripted", 7, 1, 0, Decimal("0"), START)])
    finally:
        service.client.headers["x-workspace-id"] = original_workspace
    async with transaction(service.runtime.storage) as session:
        first_ws = await session.get_one(WorkspaceRow, original_workspace)
        other_ws = await session.get_one(WorkspaceRow, other_id)
        other_ws.name = first_ws.name  # Display-name collisions must not merge immutable IDs.
        assert (await session.get_one(AgentRow, agent["id"])).name == (
            await session.get_one(AgentRow, other_agent["id"])
        ).name

    async def read(workspaces, *, start=START, end=START + timedelta(days=3)):
        result = {}
        async with short_session(service.runtime.storage) as session:
            # Force a non-UTC session: SQL must still produce UTC calendar buckets.
            await session.execute(text("SET TIME ZONE 'America/Los_Angeles'"))
            for title, sql in usage_queries().items():
                result[title] = (
                    (
                        await session.execute(
                            text(
                                expand_usage(
                                    sql,
                                    organizations=[service.tenant.organization_id],
                                    workspaces=workspaces,
                                    start=start,
                                    end=end,
                                )
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
        return result

    only = await read([original_workspace])
    assert only["Known recorded cost"][0]["known_cost_usd"] == Decimal("0.3")
    assert only["Unpriced requests"][0]["unpriced_requests"] == 2
    assert only["Pricing coverage"][0]["pricing_coverage"] == Decimal("0.5")
    assert only["Model requests"][0]["requests"] == 4
    assert only["Started runs"][0]["runs"] == 2
    assert only["Average sealed runtime"][0]["average_duration_seconds"] == 20
    assert sum(row["input_tokens"] for row in only["Daily model usage"]) == 1150
    assert [row["time"].astimezone(START.tzinfo).day for row in only["Daily model usage"]] == [1, 2, 3]
    model = only["Model usage by workspace and model"][0]
    assert model["cache_hit_rate"] == pytest.approx(Decimal(230) / Decimal(1100))
    assert sum(row["requests"] for row in only["Usage by agent"]) == 4
    assert len(only["Usage by agent"]) == 1  # Zero-request Agent is not in the consumption population.
    both = await read([original_workspace, other_id])
    assert len(both["Runs by workspace"]) == 2
    assert len({row["workspace_id"] for row in both["Runs by workspace"]}) == 2
    scripted = [row for row in both["Model usage by workspace and model"] if row["model"] == "scripted"]
    assert len(scripted) == 2 and len({row["model_id"] for row in scripted}) == 2
    assert len(both["Usage by agent"]) == 2
    assert (await read([other_id]))["Pricing coverage"][0]["pricing_coverage"] == 1  # Known zero cost is priced.
    unknown = await read([original_workspace], start=START + timedelta(days=1), end=START + timedelta(days=2))
    assert unknown["Known recorded cost"][0]["known_cost_usd"] is None
    empty = await read([original_workspace], start=START - timedelta(days=10), end=START - timedelta(days=9))
    assert empty["Known recorded cost"][0]["known_cost_usd"] == 0
    assert empty["Pricing coverage"][0]["pricing_coverage"] is None
    assert empty["Average sealed runtime"][0]["average_duration_seconds"] is None
    assert empty["Runs by workspace"] == []
    # Date categories retain partial UTC days instead of clipping midnight out of a short chart range.
    partial = await read([original_workspace], start=START - timedelta(minutes=1), end=START + timedelta(minutes=1))
    assert partial["Daily known cost"] == [{"day": "2026-09-01", "known_cost_usd": Decimal("0.3")}]
    assert partial["Daily tokens"] == [{"day": "2026-09-01", "input_tokens": 200, "output_tokens": 25}]
    # A later corrected report revises its first ingestion bucket; it is not another request.
    async with transaction(service.runtime.storage) as session:
        record = await session.scalar(
            select(UsageRecordRow).where(
                UsageRecordRow.run_id == first_run,
                UsageRecordRow.ingested_at == START,
            )
        )
        # Mutable Harness contributions have a producer cursor in the same scope;
        # legacy facts without a cursor deliberately cannot be corrected.
        session.add(
            UsageRecordRow(
                id="scope_dashboard_correction",
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                run_id=record.run_id,
                run_attempt_id=record.run_attempt_id,
                harness_run_id=record.harness_run_id,
                digest="fixture-cursor",
                record={
                    "kind": "cursor",
                    "usage_id": "scope_dashboard_correction",
                    "run_id": record.harness_run_id,
                    "agent_instance_id": "root",
                    "sequence": 1,
                    "tool_calls": 0,
                },
            )
        )
        await session.flush()
        record.record = {**record.record, "request_usage": {**record.record["request_usage"], "cost": "0.4"}}
    corrected = await read([original_workspace])
    assert corrected["Known recorded cost"][0]["known_cost_usd"] == Decimal("0.6")
    assert corrected["Model requests"][0]["requests"] == 4
    assert corrected["Daily model usage"][0]["known_cost_usd"] == Decimal("0.6")
