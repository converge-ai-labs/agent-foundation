"""Workspace aggregates observe current facts, independent Run populations and query-bound pages."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx2
import pytest
from a13n_harness.usage import BoundedRequestUsage, ModelUsageRecord, UsageDelta, UsageScope
from a13n_service.infra.db import transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.models.tables import ModelRow
from a13n_service.runs.claim import claim
from a13n_service.runs.tables import AttemptRow, RunRow, ThreadRow, UsageRecordRow
from a13n_service.runs.usage import ingest_delta
from sqlalchemy import select

pytestmark = pytest.mark.anyio
START = datetime(2026, 9, 1, tzinfo=UTC)
WINDOW = {"start": START.isoformat(), "end": (START + timedelta(days=3)).isoformat()}


async def observed_run(service, kit, agent, *, started=START, seconds=10, records=()):  # type: ignore[no-untyped-def]
    submitted = await kit.start_thread(service, agent, "Usage fixture")
    run_id = submitted["run"]["id"]
    [lease] = await claim(service.runtime, worker_id="usage-fixture", worker_build="test", limit=1)
    async with transaction(service.runtime.storage) as session:
        run = await session.get_one(RunRow, run_id)
        attempt = await session.get_one(AttemptRow, lease.attempt_id)
        thread = await session.get_one(ThreadRow, run.thread_id)
        end = started + timedelta(seconds=seconds)
        run.started_at, run.sealed_at = started, end
        run.status, run.failure, run.current_attempt_id = "failed", {"code": "fixture", "message": "Fixture"}, None
        attempt.status, attempt.started_at, attempt.finished_at = "failed", started, end
        thread.current_run_id = None
        thread.last_run_id = run_id
        for model, input_tokens, output_tokens, cached, cost, ingested in records:
            model_id = await session.scalar(select(ModelRow.id).where(ModelRow.key == model)) if model else None
            record = ModelUsageRecord(
                record_id=new_object_id("usage"),
                run_id=lease.attempt_id,
                agent_instance_id="root",
                response_ordinal=0,
                response_state="complete",
                response_timestamp=ingested,
                request_usage=BoundedRequestUsage(
                    input_tokens=input_tokens, output_tokens=output_tokens, cache_read_tokens=cached, cost=cost
                ),
            )
            session.add(
                UsageRecordRow(
                    id=record.record_id,
                    organization_id=run.organization_id,
                    workspace_id=run.workspace_id,
                    run_id=run_id,
                    run_attempt_id=attempt.id,
                    harness_run_id=record.run_id,
                    digest="fixture",
                    record=record.model_dump(mode="json"),
                    model_id=model_id,
                    ingested_at=ingested,
                )
            )
    return run_id, lease.attempt_id


async def read(service, path="overview", **params):  # type: ignore[no-untyped-def]
    response = await service.client.get(f"{service.api}/usage/{path}", params={**WINDOW, **params})
    assert response.status_code == 200, response.text
    return response.json()


async def test_overview_and_groups_use_distinct_populations(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    second = await runs_kit.add_agent(service, "Empty run", "scripted")
    records = [
        ("scripted", 100, 20, 90, Decimal("0.1"), START),
        ("scripted", 900, 30, 90, None, START + timedelta(days=1)),
        (None, 50, 10, 0, None, START + timedelta(days=2)),
        ("scripted", 9999, 0, 0, Decimal("99"), START + timedelta(days=3)),
    ]
    await observed_run(service, runs_kit, agent, records=records)
    await observed_run(service, runs_kit, second, seconds=30)
    # Consumption arrives in this window, but this Run began outside it.
    await observed_run(
        service,
        runs_kit,
        agent,
        started=START - timedelta(days=1),
        records=[("scripted", 100, 5, 50, Decimal("0.2"), START)],
    )
    overview = await read(service)
    assert overview["runs"] == {"runs": 2, "average_duration_seconds": 20}
    assert overview["usage"] == {
        "requests": 4,
        "input_tokens": 1150,
        "output_tokens": 65,
        "cache_read_tokens": 230,
        "cache_hit_rate": 0.2,
        "cost": "0.3",
        "unpriced_requests": 2,
    }
    assert sum(day["usage"]["input_tokens"] for day in overview["daily"]) == 1150
    assert [day["date"] for day in overview["daily"]] == ["2026-09-01", "2026-09-02", "2026-09-03"]
    agents = (await read(service, "agents"))["items"]
    assert len(agents) == 2 and sum(row["runs"]["runs"] for row in agents) == 2
    assert agents[1]["usage"]["cost"] == "0" and agents[1]["usage"]["requests"] == 0
    models = (await read(service, "models"))["items"]
    assert [row["model"] for row in models] == ["scripted", None]
    assert models[1]["usage"]["cost"] is None
    assert sum(row["usage"]["requests"] for row in models) == 4
    first = await read(service, "models", limit=1)
    last = await read(service, "models", limit=1, cursor=first["next_cursor"])
    assert first["items"] + last["items"] == models and last["next_cursor"] is None
    # The original summary endpoint keeps its shape and includes cache writes.
    summary = (
        await service.client.get(
            f"{service.api}/usage",
            params={
                "ingested_after": WINDOW["start"],
                "ingested_before": WINDOW["end"],
            },
        )
    ).json()
    assert sum(row["requests"] for row in summary["models"]) == 4
    assert all("cache_write_tokens" in row for row in summary["models"])


async def test_calendar_buckets_empty_state_and_invalid_windows(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    empty = await read(service)
    assert empty["usage"]["cost"] == "0" and empty["usage"]["cache_hit_rate"] is None
    assert empty["runs"]["average_duration_seconds"] is None
    assert len(empty["daily"]) == 3 and all(day["usage"]["requests"] == 0 for day in empty["daily"])
    agent = await runs_kit.create_agent(service, scripted_model)
    await observed_run(
        service,
        runs_kit,
        agent,
        records=[
            ("scripted", 10, 0, 0, Decimal(0), START + timedelta(hours=17)),
        ],
    )
    local = await read(service, timezone="Asia/Shanghai")
    assert local["daily"][1]["date"] == "2026-09-02" and local["daily"][1]["usage"]["requests"] == 1
    for invalid in (
        {"start": WINDOW["end"]},
        {"timezone": "Mars/Olympus"},
        {"start": "2020-01-01T00:00:00Z"},
        {"start": "2026-09-01"},
    ):
        response = await service.client.get(f"{service.api}/usage/overview", params={**WINDOW, **invalid})
        assert response.status_code == 400


async def test_cost_order_and_cursor_scope(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    model = await runs_kit.create_model(service, scripted_model)
    ids = []
    for name, cost in (("A", Decimal("0.2")), ("B", Decimal("0.2")), ("C", Decimal(0)), ("D", None)):
        agent = await runs_kit.add_agent(service, name, model)
        ids.append(agent["id"])
        await observed_run(service, runs_kit, agent, records=[(model, 10, 0, 0, cost, START)])
    found, cursor = [], None
    while True:
        page = await read(service, "agents", limit=1, **({"cursor": cursor} if cursor else {}))
        found.extend(item["agent_id"] for item in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert found == [*sorted(ids[:2]), *ids[2:]]
    cursor = (await read(service, "agents", limit=1))["next_cursor"]
    for path, fields in (("models", {}), ("agents", {"end": "2026-09-05T00:00:00Z"}), ("agents", {"cursor": "bad"})):
        response = await service.client.get(
            f"{service.api}/usage/{path}", params={**WINDOW, "cursor": cursor, **fields}
        )
        assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_cursor"


async def test_latest_delta_is_counted_once_and_other_workspace_is_empty(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id, attempt_id = await observed_run(service, runs_kit, agent)
    record = ModelUsageRecord(
        record_id="usage_latest",
        run_id="harness_latest",
        agent_instance_id="root",
        response_ordinal=0,
        response_state="complete",
        response_timestamp=datetime.now(UTC),
        request_usage=BoundedRequestUsage(input_tokens=10),
    )
    for seq, tokens in ((1, 10), (2, 30), (2, 30)):
        delta = UsageDelta(
            scope=UsageScope(usage_id="scope_latest", run_id=record.run_id, agent_instance_id="root", sequence=seq),
            after_sequence=seq - 1,
            records=(record.model_copy(update={"request_usage": BoundedRequestUsage(input_tokens=tokens)}),),
        )
        await ingest_delta(service.runtime.storage, run_id, attempt_id, delta, {})
    now = datetime.now(UTC)
    params = {"start": (now - timedelta(minutes=1)).isoformat(), "end": (now + timedelta(minutes=1)).isoformat()}
    overview = await read(service, **params)
    assert overview["usage"]["requests"] == 1 and overview["usage"]["input_tokens"] == 30
    response = await service.client.post(
        f"{service.api}/organizations/{service.tenant.organization_id}/workspaces", json={"name": "Empty"}
    )
    assert response.status_code == 201, response.text
    other = await service.client.get(
        f"{service.api}/usage/overview", params=params, headers={"X-Workspace-ID": response.json()["id"]}
    )
    assert other.status_code == 200 and other.json()["usage"]["requests"] == 0


async def test_usage_requires_workspace_read_permission(service):  # type: ignore[no-untyped-def]
    other = (await service.client.post(f"{service.organization}/workspaces", json={"name": "Private"})).json()
    invitation = await service.client.post(
        f"{service.workspace}/invitations", json={"email": "usage-reader@example.com", "role": "viewer"}
    )
    assert invitation.status_code == 201, invitation.text
    url = invitation.json()["invitation_url"]
    path = "/api/v1/invitations/" + url.split("/invitations/", 1)[1].split("#", 1)[0]
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=service.app), base_url="https://service.test"
    ) as viewer:
        accepted = await viewer.post(
            path,
            json={"token": url.split("#token=", 1)[1], "password": "usage-viewer-password", "name": "Usage viewer"},
        )
        assert accepted.status_code == 200, accepted.text
        for endpoint in ("overview", "agents", "models"):
            path = f"{service.api}/usage/{endpoint}"
            allowed = await viewer.get(path, params=WINDOW, headers={"X-Workspace-ID": service.tenant.workspace_id})
            denied = await viewer.get(path, params=WINDOW, headers={"X-Workspace-ID": other["id"]})
            assert allowed.status_code == 200
            assert denied.status_code == 403


async def test_dst_day_uses_local_calendar_with_both_repeated_hours(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    fall_back = datetime(2026, 11, 1, tzinfo=UTC)
    await observed_run(
        service,
        runs_kit,
        agent,
        records=[("scripted", 0, 4, 0, Decimal(0), fall_back + timedelta(hours=hour)) for hour in (5, 6)],
    )
    result = await read(
        service, start="2026-11-01T00:00:00-04:00", end="2026-11-02T00:00:00-05:00", timezone="America/New_York"
    )
    assert len(result["daily"]) == 1
    assert result["daily"][0]["date"] == "2026-11-01"
    assert result["usage"]["requests"] == 2 and result["usage"]["cache_hit_rate"] is None
