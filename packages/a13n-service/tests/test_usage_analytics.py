"""Workspace aggregates observe current facts, independent Run populations and query-bound pages."""

import base64
import json
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


async def observed_run(
    service, kit, agent, *, started=START, seconds=10, records=(), record_fields=None, thread_id=None
):  # type: ignore[no-untyped-def]
    submitted = (
        await kit.start_thread(service, agent, "Usage fixture")
        if thread_id is None
        else (await kit.submit(service, thread_id, kit.message(agent, "Usage fixture"))).json()
    )
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
        for model, input_tokens, output_tokens, cached, cost, ingested, *timing in records:
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
                **(streamed(*timing[0]) if timing else {}),
            )
            if record_fields:
                record = record.model_copy(update=record_fields)
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


def streamed(waited: float, generated: float) -> dict[str, datetime]:
    first_chunk = START + timedelta(seconds=waited)
    return {
        "request_started_at": START,
        "first_chunk_at": first_chunk,
        "request_ended_at": first_chunk + timedelta(seconds=generated),
    }


async def read(service, path="overview", **params):  # type: ignore[no-untyped-def]
    response = await service.client.get(f"{service.api}/usage/{path}", params={**WINDOW, **params})
    assert response.status_code == 200, response.text
    return response.json()


async def assert_invalid_numeric_cursors(service, path, params, cursor):
    """An opaque cursor is still untrusted input: reject invalid positions before SQL."""
    position = json.loads(base64.urlsafe_b64decode(cursor))
    for index, value in (
        (2, "1e999999"),
        (2, "1e-999999"),
        (2, "NaN"),
        (2, "Infinity"),
        (2, 1),
        (len(position) - 1, "x" * 129),
    ):
        malformed = position.copy()
        malformed[index] = value
        token = base64.urlsafe_b64encode(json.dumps(malformed).encode()).decode()
        response = await service.client.get(path, params={**params, "cursor": token})
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "invalid_cursor"


async def test_overview_and_groups_use_distinct_populations(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    second = await runs_kit.add_agent(service, "Empty run", "scripted")
    records = [
        ("scripted", 100, 20, 90, Decimal("0.1"), START, (1, 2)),
        ("scripted", 900, 30, 90, None, START + timedelta(days=1), (3, 1)),
        (None, 50, 10, 0, None, START + timedelta(days=2)),
        ("scripted", 9999, 0, 0, Decimal("99"), START + timedelta(days=3)),
    ]
    first_run, _ = await observed_run(service, runs_kit, agent, records=records)
    await observed_run(service, runs_kit, second, seconds=30)
    # Consumption arrives in this window, but this Run began outside it.
    late_run, _ = await observed_run(
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
    # Summary and Run pages use the same aggregation policy, including cache writes.
    summary = (
        await service.client.get(
            f"{service.api}/usage",
            params={
                "ingested_after": WINDOW["start"],
                "ingested_before": WINDOW["end"],
            },
        )
    ).json()
    assert [(row["model"], row["requests"]) for row in summary["models"]] == [("scripted", 3), (None, 1)]
    assert all("cache_write_tokens" in row for row in summary["models"])
    # Only the first Run has request timing; include the wait before its first chunks.
    timing = [
        (row["average_time_to_first_chunk_seconds"], row["output_tokens_per_second"]) for row in summary["models"]
    ]
    assert timing == [(2, pytest.approx(50 / 7)), (None, None)]
    runs = (
        await service.client.get(
            f"{service.api}/usage/runs", params={"ingested_after": WINDOW["start"], "ingested_before": WINDOW["end"]}
        )
    ).json()["items"]
    assert [run["run_id"] for run in runs] == [first_run, late_run]
    first = runs[0]
    assert [(row["model"], row["requests"], row["input_tokens"], row["cost"]) for row in first["models"]] == [
        ("scripted", 2, 1000, "0.1"),
        (None, 1, 50, None),
    ]
    run = (await service.client.get(f"{service.api}/runs/{first_run}")).json()
    assert first["thread_id"] == run["thread_id"]
    for field in ("requests", "input_tokens", "output_tokens", "cache_read_tokens"):
        assert sum(row[field] for run in runs for row in run["models"]) == sum(row[field] for row in summary["models"])
    only = (await service.client.get(f"{service.api}/usage", params={"run_id": late_run})).json()
    only_runs = (await service.client.get(f"{service.api}/usage/runs", params={"run_id": late_run})).json()["items"]
    assert [run["run_id"] for run in only_runs] == [late_run] and only["models"] == only_runs[0]["models"]
    assert only["models"][0]["output_tokens_per_second"] is None


@pytest.mark.parametrize(
    ("outcome", "duration", "first_chunk", "expected"),
    [
        ("completed", 10, 8, 10),
        ("completed", 10, None, 10),
        ("completed", 10, 10, 10),
        ("completed", 0, None, None),
        ("completed", -1, None, None),
        ("completed", None, None, None),
        ("failed", 10, 8, None),
        ("cancelled", 10, 8, None),
    ],
)
async def test_speed_uses_full_request_duration_and_inclusive_output(
    service, scripted_model, runs_kit, outcome, duration, first_chunk, expected
):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id, _ = await observed_run(
        service,
        runs_kit,
        agent,
        records=[("scripted", 20, 100, 0, None, START)],
        record_fields={
            "outcome": outcome,
            "request_started_at": START,
            "request_ended_at": START + timedelta(seconds=duration) if duration is not None else None,
            "first_chunk_at": START + timedelta(seconds=first_chunk) if first_chunk is not None else None,
            # Provider output includes 60 reasoning tokens; adding the detail again would yield 16/s.
            "request_usage": BoundedRequestUsage(input_tokens=20, output_tokens=100, details={"reasoning_tokens": 60}),
        },
    )
    response = await service.client.get(f"{service.api}/usage", params={"run_id": run_id})
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["models"][0]["output_tokens_per_second"] == expected
    runs = (await service.client.get(f"{service.api}/usage/runs", params={"run_id": run_id})).json()["items"]
    assert runs[0]["models"] == summary["models"]
    async with transaction(service.runtime.storage) as session:
        record_id = await session.scalar(select(UsageRecordRow.id).where(UsageRecordRow.run_id == run_id))
    response = await service.client.get(f"{service.api}/usage/records/{record_id}/metrics")
    assert response.status_code == 200, response.text
    metrics = response.json()
    assert metrics["output_tokens_per_second"] == expected
    assert metrics["request_duration_seconds"] == (duration if expected is not None else None)
    assert metrics["time_to_first_chunk_seconds"] == summary["models"][0]["average_time_to_first_chunk_seconds"]


async def test_request_metrics_hide_other_workspaces_and_missing_records(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id, _ = await observed_run(service, runs_kit, agent, records=[("scripted", 20, 0, 0, None, START, (1, 2))])
    async with transaction(service.runtime.storage) as session:
        record_id = await session.scalar(select(UsageRecordRow.id).where(UsageRecordRow.run_id == run_id))
    path = f"{service.api}/usage/records/{record_id}/metrics"
    response = await service.client.get(path)
    assert response.status_code == 200
    assert response.json()["output_tokens_per_second"] == 0
    other = (await service.client.post(f"{service.organization}/workspaces", json={"name": "Other"})).json()
    response = await service.client.get(path, headers={"X-Workspace-ID": other["id"]})
    assert response.status_code == 404
    assert (await service.client.get(f"{service.api}/usage/records/missing/metrics")).status_code == 404


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
    await assert_invalid_numeric_cursors(service, f"{service.api}/usage/agents", WINDOW, cursor)
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
        for endpoint in ("overview", "agents", "models", "records/missing/metrics"):
            path = f"{service.api}/usage/{endpoint}"
            allowed = await viewer.get(path, params=WINDOW, headers={"X-Workspace-ID": service.tenant.workspace_id})
            denied = await viewer.get(path, params=WINDOW, headers={"X-Workspace-ID": other["id"]})
            assert allowed.status_code == (404 if endpoint.startswith("records/") else 200)
            assert denied.status_code == 403
        assert (
            await viewer.get(
                f"{service.api}/usage/members", params=WINDOW, headers={"X-Workspace-ID": service.tenant.workspace_id}
            )
        ).status_code == 403
        for endpoint in ("overview", "agents", "models"):
            for principal_id in (service.tenant.principal_id, "usr_00000000000000000000"):
                assert (
                    await viewer.get(
                        f"{service.api}/usage/{endpoint}",
                        params={**WINDOW, "principal_id": principal_id},
                        headers={"X-Workspace-ID": service.tenant.workspace_id},
                    )
                ).status_code == 403


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


async def test_context_usage_selects_main_call_and_current_capacity(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(
        service, scripted_model, model_characteristics={"context_window_tokens": 100000}
    )
    run_id, _ = await observed_run(
        service,
        runs_kit,
        agent,
        records=[("scripted", 40000, 2000, 30000, None, START)],
    )
    async with transaction(service.runtime.storage) as session:
        original = await session.scalar(select(UsageRecordRow).where(UsageRecordRow.run_id == run_id))
        assert original is not None
        model = await session.get_one(ModelRow, original.model_id)
        model.config = {**model.config, "characteristics": {"context_window_tokens": 200000}}
        run = await session.get_one(RunRow, run_id)
        thread_id = run.thread_id
        record_id = original.id
        # A late older report, inline child, compaction and failed call cannot replace the main call.
        for ordinal, extra in enumerate(
            [
                {"response_ordinal": 0},
                {"parent_agent_instance_id": "root", "agent_instance_id": "child"},
                {"source": "compaction"},
                {"outcome": "failed"},
            ],
            start=2,
        ):
            child_id = new_object_id("usage")
            if ordinal == 3:
                inline_id = child_id
            payload = {
                **original.record,
                "record_id": child_id,
                "response_ordinal": ordinal,
                "request_usage": {"input_tokens": 90000, "output_tokens": 1000},
                **extra,
            }
            # The older record has a lower ordinal than the selected main call below.
            session.add(
                UsageRecordRow(
                    id=child_id,
                    organization_id=original.organization_id,
                    workspace_id=original.workspace_id,
                    run_id=run_id,
                    run_attempt_id=original.run_attempt_id,
                    harness_run_id=original.harness_run_id,
                    record=payload,
                    digest="fixture",
                    model_id=original.model_id,
                    ingested_at=START + timedelta(days=1),
                )
            )
        # Original ordinal zero is older; a genuine next main response must win by ordinal, not ingestion.
        latest_id = new_object_id("usage")
        session.add(
            UsageRecordRow(
                id=latest_id,
                organization_id=original.organization_id,
                workspace_id=original.workspace_id,
                run_id=run_id,
                run_attempt_id=original.run_attempt_id,
                harness_run_id=original.harness_run_id,
                record={**original.record, "record_id": latest_id, "response_ordinal": 1},
                digest="fixture",
                model_id=original.model_id,
                ingested_at=START,
            )
        )
    for scope in ({"run_id": run_id}, {"thread_id": thread_id}, {"record_id": latest_id}):
        response = await service.client.get(f"{service.api}/usage/context", params=scope)
        assert response.status_code == 200, response.text
        assert response.json() == {
            "run_id": run_id,
            "record_id": latest_id,
            "input_tokens": 40000,
            "output_tokens": 2000,
            "context_window_tokens": 100000,
        }
    response = await service.client.get(f"{service.api}/usage/context", params={"record_id": inline_id})
    assert response.status_code == 200
    assert response.json()["context_window_tokens"] == 200000
    receipt = await runs_kit.submit(
        service,
        thread_id,
        {
            "kind": "message",
            "delivery": "next_run",
            "agent_id": agent["id"],
            "payload": {"content": [{"type": "text", "text": "Next round"}]},
            "options": {"overrides": {"model_characteristics": {"context_window_tokens": 32000}}},
        },
    )
    assert receipt.status_code == 201, receipt.text
    response = await service.client.get(f"{service.api}/usage/context", params={"thread_id": thread_id})
    assert response.status_code == 200
    assert response.json() == {
        "run_id": receipt.json()["run"]["id"],
        "record_id": None,
        "input_tokens": None,
        "output_tokens": None,
        "context_window_tokens": 32000,
    }
    response = await service.client.get(f"{service.api}/usage/context", params={"run_id": run_id})
    assert response.json()["record_id"] == latest_id
    assert response.json()["context_window_tokens"] == 100000
    other = (await service.client.post(f"{service.organization}/workspaces", json={"name": "Other"})).json()
    for scope in ({"run_id": run_id}, {"thread_id": thread_id}, {"record_id": record_id}):
        response = await service.client.get(
            f"{service.api}/usage/context", params=scope, headers={"X-Workspace-ID": other["id"]}
        )
        assert response.status_code == 404
    for scope in ({}, {"run_id": run_id, "thread_id": thread_id}):
        response = await service.client.get(f"{service.api}/usage/context", params=scope)
        assert response.status_code == 400


@pytest.mark.parametrize("window", [None, 100000])
async def test_context_usage_preserves_unknowns_and_agent_override(service, scripted_model, runs_kit, window):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(
        service, scripted_model, model_characteristics={"context_window_tokens": window}
    )
    run_id, _ = await observed_run(service, runs_kit, agent, records=[("scripted", 0, 0, 0, None, START)])
    async with transaction(service.runtime.storage) as session:
        model = await session.scalar(select(ModelRow).where(ModelRow.key == "scripted"))
        assert model is not None
        model.config = {**model.config, "characteristics": {"context_window_tokens": 200000}}
    expected_window = window or 200000
    response = await service.client.get(f"{service.api}/usage/context", params={"run_id": run_id})
    assert response.status_code == 200, response.text
    assert response.json()["input_tokens"] is None
    assert response.json()["output_tokens"] is None
    assert response.json()["context_window_tokens"] == expected_window
    empty_id, _ = await observed_run(service, runs_kit, agent)
    response = await service.client.get(f"{service.api}/usage/context", params={"run_id": empty_id})
    assert response.status_code == 200
    assert response.json() == {
        "run_id": empty_id,
        "record_id": None,
        "input_tokens": None,
        "output_tokens": None,
        "context_window_tokens": expected_window,
    }
    response = await service.client.get(f"{service.api}/usage/context", params={"record_id": "missing"})
    assert response.status_code == 404


async def test_usage_pages_number_runs_without_matching_usage(service, scripted_model, runs_kit):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    empty, _ = await observed_run(service, runs_kit, agent)
    thread_id = (await service.client.get(f"{service.api}/runs/{empty}")).json()["thread_id"]
    run_id, _ = await observed_run(
        service,
        runs_kit,
        agent,
        thread_id=thread_id,
        records=[("scripted", 100, 5, 50, Decimal("0.2"), START)],
    )
    for filters in ({"thread_id": thread_id}, {"run_id": run_id}, {"ingested_after": START.isoformat()}):
        response = await service.client.get(f"{service.api}/usage/runs", params=filters)
        assert response.status_code == 200, response.text
        assert [(run["run_id"], run["run_number"]) for run in response.json()["items"]] == [(run_id, 2)]


async def test_totals_and_run_pages_preserve_numbers_and_whole_model_groups(service, scripted_model, runs_kit):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    empty, _ = await observed_run(service, runs_kit, agent)
    thread = (await service.client.get(f"{service.api}/runs/{empty}")).json()
    records = [("scripted", 100, 5, 50, Decimal("0.2"), START), (None, 3, 2, 0, None, START)]
    for _ in range(2):
        await observed_run(service, runs_kit, agent, thread_id=thread["thread_id"], records=records)
    scope = {"session_id": thread["session_id"]}
    totals = await service.client.get(f"{service.api}/usage", params=scope)
    assert totals.status_code == 200, totals.text
    assert set(totals.json()) == {"models"}
    first = await service.client.get(f"{service.api}/usage/runs", params={**scope, "limit": 1})
    assert first.status_code == 200, first.text
    page = first.json()
    assert page["items"][0]["run_number"] == 2 and len(page["items"][0]["models"]) == 2
    last = await service.client.get(
        f"{service.api}/usage/runs", params={**scope, "cursor": page["next_cursor"], "limit": 1}
    )
    assert last.status_code == 200, last.text
    assert last.json()["next_cursor"] is None
    assert len(last.json()["items"]) == 1 and last.json()["items"][0]["run_number"] == 3
    combined = page["items"] + last.json()["items"]
    for field in ("requests", "input_tokens", "output_tokens", "cache_read_tokens"):
        assert sum(model[field] for run in combined for model in run["models"]) == sum(
            model[field] for model in totals.json()["models"]
        )
    changed = await service.client.get(
        f"{service.api}/usage/runs", params={"thread_id": thread["thread_id"], "cursor": page["next_cursor"]}
    )
    assert changed.status_code == 400


async def test_members_attribute_execution_identity_and_filter_all_views(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    from a13n_service.tenancy.tables import PrincipalRow

    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    user_id = service.tenant.principal_id
    await observed_run(service, runs_kit, agent, records=[("scripted", 100, 20, 50, Decimal("0.2"), START)])
    account = (await service.client.post(f"{service.workspace}/service-accounts", json={"name": "Shared bot"})).json()
    key = (
        await service.client.post(f"{service.workspace}/service-accounts/{account['id']}/keys", json={"name": "test"})
    ).json()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=service.app),
        base_url="https://service.test",
        headers={"authorization": "Bearer " + key["secret"]},
    ) as client:
        bot = SimpleNamespace(**{**vars(service), "client": client})
        await observed_run(
            bot,
            runs_kit,
            agent,
            seconds=30,
            records=[("scripted", 200, 40, 100, Decimal("0.4"), START), ("scripted", 400, 80, 100, None, START)],
        )
        # Late usage follows ingestion rather than Run start.
        await observed_run(
            bot,
            runs_kit,
            agent,
            started=START - timedelta(days=10),
            records=[("scripted", 50, 10, 0, Decimal("0.1"), START)],
        )
        assert (await client.get(f"{service.api}/usage/members", params=WINDOW)).status_code == 403
        for endpoint in ("overview", "agents", "models"):
            assert (
                await client.get(f"{service.api}/usage/{endpoint}", params={**WINDOW, "principal_id": account["id"]})
            ).status_code == 403
    async with transaction(service.runtime.storage) as session:
        principal = await session.get_one(PrincipalRow, account["id"])
        principal.status = "disabled"
    groups = (await read(service, "members"))["items"]
    assert [row["principal_id"] for row in groups] == [account["id"], user_id]
    assert [(row["kind"], row["status"]) for row in groups] == [("service_account", "disabled"), ("user", "active")]
    assert groups[0]["runs"] == {"runs": 1, "average_duration_seconds": 30}
    assert groups[0]["usage"]["requests"] == 3
    assert Decimal(groups[0]["usage"]["cost"]) == Decimal("0.5")
    total = await read(service)
    for metric in ("requests", "input_tokens", "output_tokens", "cache_read_tokens", "unpriced_requests"):
        assert sum(row["usage"][metric] for row in groups) == total["usage"][metric]
    for principal_id, requests in ((user_id, 1), (account["id"], 3)):
        overview = await read(service, principal_id=principal_id)
        assert overview["usage"]["requests"] == requests
        assert sum(day["usage"]["requests"] for day in overview["daily"]) == requests
        for endpoint in ("agents", "models"):
            items = (await read(service, endpoint, principal_id=principal_id))["items"]
            assert sum(item["usage"]["requests"] for item in items) == requests
    first = await read(service, "members", limit=1)
    second = await read(service, "members", limit=1, cursor=first["next_cursor"])
    assert second["items"][0]["principal_id"] == user_id
    assert (
        await service.client.get(
            f"{service.api}/usage/members", params={**WINDOW, "cursor": first["next_cursor"], "principal_id": user_id}
        )
    ).status_code == 400
    # Neither missing nor foreign identities reveal a global principal's profile.
    other = (await service.client.post(f"{service.organization}/workspaces", json={"name": "Elsewhere"})).json()
    foreign = (
        await service.client.post(f"/api/v1/workspaces/{other['id']}/service-accounts", json={"name": "Foreign"})
    ).json()
    for principal_id in ("usr_00000000000000000000", foreign["id"]):
        assert (await read(service, "members", principal_id=principal_id))["items"] == []
        assert (await read(service, principal_id=principal_id))["usage"]["requests"] == 0
    assert (
        await service.client.get(f"{service.api}/usage/members", params=WINDOW, headers={"X-Workspace-ID": other["id"]})
    ).json()["items"] == []


async def test_member_with_only_runs_is_counted_without_model_usage(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    await observed_run(service, runs_kit, agent, seconds=25)
    await observed_run(service, runs_kit, agent, started=START + timedelta(days=3))
    [member] = (await read(service, "members"))["items"]
    assert member["principal_id"] == service.tenant.principal_id
    assert member["usage"]["requests"] == 0
    assert Decimal(member["usage"]["cost"]) == 0
    assert member["runs"] == {"runs": 1, "average_duration_seconds": 25}
    assert (await read(service, "models", principal_id=member["principal_id"]))["items"] == []


async def test_session_metric_sorting_pages_exact_totals_and_keeps_unknown_last(service, scripted_model, runs_kit):
    agent = await runs_kit.create_agent(service, scripted_model)
    expected = []
    for cost in (Decimal("0.10000000000000000001"), Decimal("0.1"), Decimal("0.1"), Decimal(0), None):
        records = [("scripted", 10, 0, 0, cost, START)]
        if cost == Decimal("0.1"):
            records.append(("scripted", 10, 0, 0, None, START))
        run_id, _ = await observed_run(service, runs_kit, agent, records=records)
        run = (await service.client.get(f"{service.api}/runs/{run_id}")).json()
        expected.append((run["session_id"], cost, 1))
    empty = (await service.client.post(f"{service.api}/sessions", json={"title": "Empty"})).json()
    expected.append((empty["id"], None, 0))
    await runs_kit.start_thread(service, agent, "Second run", session_id=expected[0][0])
    expected[0] = (*expected[0][:2], 2)

    for field, position in (("cost", 1), ("runs", 2)):
        for direction in ("asc", "desc"):

            def order(item, position=position, direction=direction):
                value = item[position]
                return value is None, (value or 0) * (-1 if direction == "desc" else 1), item[0]

            wanted = sorted(expected, key=order)
            cursor = None
            actual = []
            while True:
                params = {"sort": field, "direction": direction, "limit": 2}
                if cursor:
                    params["cursor"] = cursor
                response = await service.client.get(f"{service.api}/sessions", params=params)
                assert response.status_code == 200, response.text
                page = response.json()
                actual.extend(page["items"])
                cursor = page["next_cursor"]
                if not cursor:
                    break
                wrong = await service.client.get(
                    f"{service.api}/sessions",
                    params={**params, "cursor": cursor, "direction": "asc" if direction == "desc" else "desc"},
                )
                assert wrong.status_code == 400
            assert [item["id"] for item in actual] == [item[0] for item in wanted]
            for item, (_, cost, count) in zip(actual, wanted, strict=True):
                assert (Decimal(item["cost"]) if item["cost"] is not None else None) == cost
                assert item["run_count"] == count
                if cost == Decimal("0.1"):
                    assert item["unpriced_requests"] == 1
    filtered = await service.client.get(f"{service.api}/sessions", params={"sort": "cost", "q": "Empty"})
    assert [item["id"] for item in filtered.json()["items"]] == [empty["id"]]
    params = {"sort": "cost", "limit": 1}
    first = (await service.client.get(f"{service.api}/sessions", params=params)).json()
    await assert_invalid_numeric_cursors(service, f"{service.api}/sessions", params, first["next_cursor"])


async def test_thread_metric_ranking_is_global_exact_and_bounded(service, scripted_model, runs_kit):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    thread_id = None
    expected = []
    for cost, calls, seconds in (
        (Decimal("0.10000000000000000001"), 1, 15),
        (Decimal("0.1"), 2, 40),
        (Decimal("0.1"), 2, 40),
        (Decimal(0), 1, 5),
        (None, 1, 30),
    ):
        records = [("scripted", 10, 0, 0, cost, START)]
        records += [("scripted", 10, 0, 0, None, START)] * (calls - 1)
        run_id, _ = await observed_run(service, runs_kit, agent, records=records, thread_id=thread_id, seconds=seconds)
        run = (await service.client.get(f"{service.api}/runs/{run_id}")).json()
        thread_id = run["thread_id"]
        expected.append({"id": run_id, "cost": cost, "calls": calls, "duration": seconds, "number": len(expected) + 1})
    pending = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "Not yet started"))
    expected.append({"id": pending.json()["run"]["id"], "cost": None, "calls": 0, "duration": None, "number": 6})
    path = f"{service.api}/threads/{thread_id}/runs"
    for field in ("cost", "calls", "duration"):
        for direction in ("asc", "desc"):

            def order(item, field=field, direction=direction):
                value = item[field]
                return value is None, (value or 0) * (-1 if direction == "desc" else 1), item["number"]

            wanted = sorted(expected, key=order)
            actual, cursor = [], None
            while True:
                params = {"sort": field, "direction": direction, "limit": 2}
                if cursor:
                    params["cursor"] = cursor
                response = await service.client.get(path, params=params)
                assert response.status_code == 200, response.text
                page = response.json()
                assert page["total"] == 6 and len(page["items"]) <= 2
                assert page["previous_cursor"] is None
                actual.extend(page["items"])
                cursor = page["next_cursor"]
                if not cursor:
                    break
                mismatch = await service.client.get(
                    path, params={**params, "cursor": cursor, "direction": "asc" if direction == "desc" else "desc"}
                )
                assert mismatch.status_code == 400
            assert [item["id"] for item in actual] == [item["id"] for item in wanted]
            for item, expected_item in zip(actual, wanted, strict=True):
                assert item["number"] == expected_item["number"]
                assert item["model_calls"] == expected_item["calls"]
                assert (Decimal(item["cost"]) if item["cost"] is not None else None) == expected_item["cost"]
                assert "input" not in item
    assert (await service.client.get(path, params={"sort": "cost", "anchor": expected[0]["id"]})).status_code == 400
    chronological = (await service.client.get(path, params={"anchor": expected[2]["id"], "limit": 2})).json()
    assert [item["number"] for item in chronological["items"]] == [3, 2]
    for field in ("cost", "calls", "duration"):
        params = {"sort": field, "limit": 1}
        first = (await service.client.get(path, params=params)).json()
        await assert_invalid_numeric_cursors(service, path, params, first["next_cursor"])


async def test_duration_ranking_keeps_sealed_waiting_runs_last(service, scripted_model, runs_kit):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model, user_questions=True)
    finished, _ = await observed_run(service, runs_kit, agent, seconds=40)
    thread_id = (await runs_kit.get_run(service, finished))["thread_id"]
    submitted = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "Pick a color"))
    waiting_id = submitted.json()["run"]["id"]
    scripted_model.call("ask_user_question", {"questions": [runs_kit.QUESTION]}, call_id="call_ask")
    await (await runs_kit.attempt(service))
    waiting = await runs_kit.get_run(service, waiting_id)
    assert waiting["status"] == "waiting" and waiting["sealed_at"] is not None
    path = f"{service.api}/threads/{thread_id}/runs"
    for direction in ("asc", "desc"):
        params = {"sort": "duration", "direction": direction, "limit": 1}
        first = (await service.client.get(path, params=params)).json()
        assert [run["id"] for run in first["items"]] == [finished]
        second = (await service.client.get(path, params={**params, "cursor": first["next_cursor"]})).json()
        assert [run["id"] for run in second["items"]] == [waiting_id]
        assert second["next_cursor"] is None
