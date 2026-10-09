"""Finding provenance, human review, preset separation and bounded ordinary-run analysis."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.telemetry import correlation_attributes
from a13n_service.providers.traces import Span, SpanPage
from a13n_service.runs.findings import analysis
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, ExecutionAuthority, Grant, Principal, WorkspaceScope
from pydantic_ai.exceptions import ToolFailed

pytestmark = pytest.mark.anyio
TRACE = "a" * 32


def actor(service, role="admin"):
    return Principal(
        service.tenant.principal_id, "user", (Grant(service.tenant.organization_id, None, BUILT_IN_ROLES[role]),)
    )


class Backend:
    type = "logfire"

    def __init__(self, service, run_id):
        self.requests = []
        now = datetime.now(UTC)
        self.root = Span(
            trace_id=TRACE,
            id="b" * 16,
            parent_id=None,
            name="harness.run",
            kind="agent",
            started_at=now - timedelta(seconds=2),
            ended_at=now,
            status="error",
            status_message="tool unavailable",
            level="error",
            model=None,
            usage={},
            cost_usd=None,
            input={"text": "search"},
            output="done",
            attributes=correlation_attributes(
                service.tenant.organization_id, service.tenant.workspace_id, run_id=run_id
            ),
            resource_attributes={},
            scope=None,
            events=[],
            links=[],
            source_url=None,
        )

    async def query(self, query):
        self.requests.append(query)
        return SpanPage(items=[self.root] if query.trace_id in (None, TRACE) else [], next_cursor=None)


async def target(service, scripted_model, runs_kit):
    agent = await runs_kit.create_agent(service, scripted_model)
    scripted_model.say("Done")
    run = (await runs_kit.start_thread(service, agent, "ambiguous request"))["run"]
    await (await runs_kit.attempt(service))
    return agent, run


def finding(agent, run):
    return {
        "agent_id": agent["id"],
        "agent_revision_id": agent["default_revision_id"],
        "title": "Unverified completion",
        "category": "execution",
        "severity": "critical",
        "explanation": "The execution evidence does not establish completion.",
        "suggestion": "Verify tool success before reporting completion.",
        "evidence": [{"run_id": run["id"], "trace_id": TRACE}],
        "source_key": "external-1",
    }


async def test_external_submission_review_retries_and_workspace_scope(service, scripted_model, runs_kit):
    agent, run = await target(service, scripted_model, runs_kit)
    body = finding(agent, run)
    created = await service.client.post("/api/v1/findings", json=body)
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["assessment"] == "unreviewed" and row["analysis_id"] is None
    replay = await service.client.post("/api/v1/findings", json=body)
    assert replay.json()["id"] == row["id"]
    conflict = await service.client.post("/api/v1/findings", json={**body, "title": "Changed"})
    assert conflict.status_code == 409
    path = f"/api/v1/findings/{row['id']}"
    reviewed = await service.client.patch(
        path, json={"assessment": "confirmed", "closed": True}, headers=runs_kit.if_match(row)
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["closed"] and reviewed.json()["assessment"] == "confirmed"
    stale = await service.client.patch(path, json={"closed": False}, headers=runs_kit.if_match(row))
    assert stale.status_code == 412
    replay = await service.client.post("/api/v1/findings", json=body)
    assert replay.json()["assessment"] == "confirmed"
    wrong = await service.client.post(
        "/api/v1/findings", json={**body, "source_key": "wrong", "agent_revision_id": new_object_id("apr")}
    )
    assert wrong.status_code == 400, wrong.text
    # A workspace selector never exposes findings belonging to another workspace.
    workspace = await service.client.post(
        f"/api/v1/organizations/{service.tenant.organization_id}/workspaces", json={"name": "Other"}
    )
    assert workspace.status_code == 201, workspace.text
    hidden = await service.client.get(path, headers={"x-workspace-id": workspace.json()["id"]})
    assert hidden.status_code == 404


async def test_presets_coexist_and_finder_has_no_configuration_writes(service, scripted_model, runs_kit):
    await runs_kit.create_model(service, scripted_model)
    composer = await service.client.post("/api/v1/agent-composer")
    finder = await service.client.post("/api/v1/finding-agent")
    assert finder.status_code == composer.status_code == 200
    assert composer.json()["preset_kind"] == "composer" and finder.json()["preset_kind"] == "finding"
    assert composer.json()["id"] != finder.json()["id"]
    selected = await service.client.get("/api/v1/agents", params={"source": "builtin", "preset_kind": "composer"})
    assert [row["id"] for row in selected.json()["items"]] == [composer.json()["id"]]
    revision = await service.client.get(
        f"/api/v1/agents/{finder.json()['id']}/revisions/{finder.json()['default_revision_id']}"
    )
    tools = revision.json()["config"]["toolsets"]
    assert tools["traces"]["enabled"] and tools["findings"]["enabled"]
    assert not tools["configuration"]["tools"]["create_agent"]["enabled"]
    assert not tools["configuration"]["tools"]["create_revision"]["enabled"]
    again = await service.client.post("/api/v1/finding-agent")
    assert again.json()["default_revision_id"] == finder.json()["default_revision_id"]


@pytest.mark.parametrize("endpoint", ["finding-agent", "agent-composer"])
async def test_builtin_configuration_is_readonly_and_models_are_selected_automatically(
    service, scripted_model, runs_kit, endpoint
):
    model = await runs_kit.create_model(service, scripted_model)
    original = (await service.client.get(f"/api/v1/models/{model}")).json()
    alternative = await service.client.post(
        "/api/v1/models",
        json={
            "key": "alternative",
            "name": "Alternative",
            "provider_id": original["provider_id"],
            "config": original["config"],
        },
    )
    assert alternative.status_code == 201, alternative.text
    prepare = f"/api/v1/{endpoint}"
    prepared = await service.client.post(prepare)
    assert prepared.status_code == 200, prepared.text
    head = prepared.json()
    path = f"/api/v1/agents/{head['id']}"
    revision = (await service.client.get(f"{path}/revisions/{head['default_revision_id']}")).json()
    assert revision["config"]["model"] == "alternative"
    for change in ({"model": model}, {"model_settings": {"max_tokens": 2048}}, {"instructions": "Custom"}):
        denied = await service.client.post(
            f"{path}/revisions",
            json={"config": {**revision["config"], **change}},
            headers={"If-Match": prepared.headers["etag"]},
        )
        assert denied.status_code == 409 and denied.json()["error"]["details"]["reason"] == "builtin", denied.text
    repeated = await service.client.post(prepare)
    assert repeated.json()["default_revision_id"] == head["default_revision_id"]
    assert repeated.headers["etag"] == prepared.headers["etag"]
    # When the current model becomes unusable, preparation picks another usable model.
    current_model = await service.client.get("/api/v1/models/alternative")
    disabled = await service.client.patch(
        "/api/v1/models/alternative", json={"enabled": False}, headers={"If-Match": current_model.headers["etag"]}
    )
    assert disabled.status_code == 200, disabled.text
    refreshed = await service.client.post(prepare)
    assert refreshed.status_code == 200, refreshed.text
    current_revision = (await service.client.get(f"{path}/revisions/{refreshed.json()['default_revision_id']}")).json()
    assert current_revision["config"]["model"] == model
    assert current_revision["config"]["model_settings"] == {}
    for action in ("archive", f"revisions/{revision['id']}/set-default"):
        denied = await service.client.post(f"{path}/{action}", headers={"If-Match": refreshed.headers["etag"]})
        assert denied.status_code == 409


async def test_native_analysis_validates_evidence_and_derives_counts(service, scripted_model, runs_kit):
    agent, run = await target(service, scripted_model, runs_kit)
    backend = Backend(service, run["id"])
    runtime = replace(service.runtime, traces=backend)
    service.app.state.runtime = runtime
    prepared = await service.client.post("/api/v1/finding-agent")
    assert prepared.status_code == 200, prepared.text
    body = {"trace_id": TRACE}
    headers = {"Idempotency-Key": "analyze-1"}
    created = await service.client.post("/api/v1/finding-analyses", json=body, headers=headers)
    assert created.status_code == 201, created.text
    result = created.json()
    assert result["agent_id"] == agent["id"]
    assert result["selected_traces"] == [{"trace_id": TRACE, "run_id": run["id"]}]
    assert result["finding_count"] == result["cited_trace_count"] == 0
    assert "reported" not in result and "limitations" not in result
    assert (await service.client.post("/api/v1/finding-analyses", json=body, headers=headers)).status_code == 200
    assert (
        await service.client.post("/api/v1/finding-analyses", json={**body, "max_traces": 2}, headers=headers)
    ).status_code == 409
    with pytest.raises(ServiceError):
        await analysis.read_scope(runtime, actor(service), service.tenant.workspace_id, result["run_id"], "c" * 32)
    from a13n_service.runs.findings.schemas import FindingCreate
    from a13n_service.runs.findings.service import create_finding

    with pytest.raises(ServiceError):
        await create_finding(
            runtime.storage,
            actor(service),
            service.tenant.workspace_id,
            FindingCreate.model_validate(finding(agent, run)),
            source_run_id=result["run_id"],
        )
    scripted_model.call("read_trace", {"trace_id": TRACE}, call_id="read-1")
    submitted = finding(agent, run)
    submitted["source_key"] = result["id"] + ":execution"
    scripted_model.call("submit_finding", {"finding": submitted}, call_id="submit-1")
    # Two distinct diagnoses citing one trace count as two findings and one cited trace.
    scripted_model.call(
        "submit_finding", {"finding": {**submitted, "source_key": result["id"] + ":answer"}}, call_id="submit-2"
    )
    scripted_model.say("One unconfirmed issue reported.")
    await (await runs_kit.attempt(service, runtime=runtime))
    rows = (await service.client.get("/api/v1/findings")).json()["items"]
    assert len(rows) == 2 and all(row["analysis_id"] == result["id"] for row in rows)
    assert rows[0]["source_run_id"] == result["run_id"]
    status = (await service.client.get("/api/v1/finding-analyses")).json()["items"][0]
    assert status["read_trace_ids"] == [TRACE]
    assert status["finding_count"] == 2 and status["cited_trace_count"] == 1
    assert (status["session_id"], status["thread_id"]) == (result["session_id"], result["thread_id"])
    assert status["run_status"] == "completed"
    while not scripted_model.requests.empty():
        last_request = scripted_model.requests.get_nowait()
    tools = last_request["tools"]
    assert {"create_agent_revision", "report_analysis"}.isdisjoint(tool["function"]["name"] for tool in tools)


async def test_workspace_analysis_reviews_multiple_agents_and_excludes_finder_runs(service, scripted_model, runs_kit):
    first, first_run = await target(service, scripted_model, runs_kit)
    second = await runs_kit.add_agent(service, "Second", "scripted")
    scripted_model.say("Done")
    second_run = (await runs_kit.start_thread(service, second, "another task"))["run"]
    await (await runs_kit.attempt(service))
    finder = (await service.client.post("/api/v1/finding-agent")).json()
    scripted_model.say("No analysis requested")
    finder_run = (await runs_kit.start_thread(service, finder, "hello"))["run"]
    await (await runs_kit.attempt(service))

    class WorkspaceBackend(Backend):
        async def query(self, query):
            self.requests.append(query)
            return SpanPage(
                items=[root for root in self.roots if query.trace_id in (None, root.trace_id)], next_cursor=None
            )

    backend = WorkspaceBackend(service, first_run["id"])
    backend.roots = [backend.root]
    for trace_id, run_id in (
        ("b" * 32, second_run["id"]),
        ("c" * 32, finder_run["id"]),
        ("d" * 32, new_object_id("run")),
    ):
        backend.roots.append(
            backend.root.model_copy(
                update={
                    "trace_id": trace_id,
                    "attributes": correlation_attributes(
                        service.tenant.organization_id, service.tenant.workspace_id, run_id=run_id
                    ),
                }
            )
        )
    runtime = replace(service.runtime, traces=backend)
    service.app.state.runtime = runtime
    created = await service.client.post(
        "/api/v1/finding-analyses", json={}, headers={"Idempotency-Key": "workspace-analysis"}
    )
    assert created.status_code == 201, created.text
    result = created.json()
    assert result["agent_id"] is None and result["selection"]["agent_id"] is None
    assert result["selected_traces"] == [
        {"trace_id": TRACE, "run_id": first_run["id"]},
        {"trace_id": "b" * 32, "run_id": second_run["id"]},
    ]
    assert result["selection"]["started_after"] and result["selection"]["started_before"]
    replay = await service.client.post(
        "/api/v1/finding-analyses", json={}, headers={"Idempotency-Key": "workspace-analysis"}
    )
    assert replay.json()["selection"] == result["selection"]
    assert replay.json()["selected_traces"] == result["selected_traces"]
    # Two first reads in parallel must preserve both observations.
    import asyncio

    await asyncio.gather(
        analysis.record_read(runtime, actor(service), service.tenant.workspace_id, result["run_id"], TRACE),
        analysis.record_read(runtime, actor(service), service.tenant.workspace_id, result["run_id"], "b" * 32),
    )
    read_status = (await service.client.get("/api/v1/finding-analyses")).json()["items"][0]
    assert set(read_status["read_trace_ids"]) == {TRACE, "b" * 32}
    for index, (agent, run, trace_id) in enumerate(((first, first_run, TRACE), (second, second_run, "b" * 32))):
        scripted_model.call("read_trace", {"trace_id": trace_id}, call_id=f"read-{index}")
        submitted = finding(agent, run)
        submitted["evidence"][0]["trace_id"] = trace_id
        submitted["source_key"] = result["id"] + f":{index}"
        scripted_model.call("submit_finding", {"finding": submitted}, call_id=f"submit-{index}")
    scripted_model.say("Two issues reported")
    await (await runs_kit.attempt(service, runtime=runtime))
    rows = (await service.client.get("/api/v1/findings")).json()["items"]
    assert {row["agent_id"] for row in rows} == {first["id"], second["id"]}
    assert all(row["analysis_id"] == result["id"] and row["source_run_id"] == result["run_id"] for row in rows)
    status = (await service.client.get("/api/v1/finding-analyses")).json()["items"][0]
    assert status["run_status"] == "completed" and set(status["read_trace_ids"]) == {TRACE, "b" * 32}
    assert status["finding_count"] == status["cited_trace_count"] == 2
    # Repeated observations remain idempotent and cannot record unselected traces.

    await asyncio.gather(
        analysis.record_read(runtime, actor(service), service.tenant.workspace_id, result["run_id"], TRACE),
        analysis.record_read(runtime, actor(service), service.tenant.workspace_id, result["run_id"], "b" * 32),
    )
    with pytest.raises(ServiceError):
        await analysis.record_read(runtime, actor(service), service.tenant.workspace_id, result["run_id"], "c" * 32)
    refreshed = (await service.client.get("/api/v1/finding-analyses")).json()["items"][0]
    assert set(refreshed["read_trace_ids"]) == {TRACE, "b" * 32}
    while not scripted_model.requests.empty():
        last_request = scripted_model.requests.get_nowait()
    prompt = str(last_request["messages"])
    assert first["default_revision_id"] in prompt and second["default_revision_id"] in prompt
    from a13n_service.runs.findings.schemas import FindingCreate
    from a13n_service.runs.findings.service import create_finding

    # A workspace analysis still cannot cite an unselected trace or misattribute another Agent's Run.
    for evidence in (
        {"run_id": first_run["id"], "trace_id": "c" * 32},
        {"run_id": second_run["id"], "trace_id": "b" * 32},
    ):
        submitted = {**finding(first, first_run), "source_key": "invalid-evidence", "evidence": [evidence]}
        with pytest.raises(ServiceError):
            await create_finding(
                runtime.storage,
                actor(service),
                service.tenant.workspace_id,
                FindingCreate.model_validate(submitted),
                source_run_id=result["run_id"],
            )

    capped = await service.client.post(
        "/api/v1/finding-analyses", json={"max_traces": 1}, headers={"Idempotency-Key": "capped-analysis"}
    )
    assert capped.status_code == 201, capped.text
    assert capped.json()["selected_traces"] == [{"trace_id": TRACE, "run_id": first_run["id"]}]
    assert capped.json()["selection_truncated"]
    assert capped.json()["finding_count"] == capped.json()["cited_trace_count"] == 0


async def test_analysis_backend_unavailable_and_delegated_tool_authority(service, scripted_model, runs_kit):
    agent, run = await target(service, scripted_model, runs_kit)
    await service.client.post("/api/v1/finding-agent")
    unavailable = await service.client.post(
        "/api/v1/finding-analyses", json={"agent_id": agent["id"]}, headers={"Idempotency-Key": "missing-backend"}
    )
    assert unavailable.status_code == 503
    # A tool's own delegated authority is narrower than its principal's grants.
    from a13n_service.runs.findings.schemas import FindingCreate
    from a13n_service.runs.findings.tools import FindingCapability

    lease = type(
        "Lease",
        (),
        {
            "organization_id": service.tenant.organization_id,
            "workspace_id": service.tenant.workspace_id,
            "run_id": run["id"],
        },
    )()
    scope = WorkspaceScope(lease.organization_id, lease.workspace_id)
    authority = ExecutionAuthority(
        principal_id=service.tenant.principal_id,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        verbs=frozenset({"read"}),
    )
    capability = FindingCapability(
        service.runtime, lease, actor(service), authority, frozenset({"read"}), frozenset({"submit"})
    )
    with pytest.raises(ToolFailed) as denied:
        await capability.submit_finding(FindingCreate.model_validate(finding(agent, run)))
    assert "delegation" in str(denied.value).lower() or "forbidden" in str(denied.value).lower()


async def test_composer_reads_durable_finding_without_changing_the_target(service, scripted_model, runs_kit):
    agent, run = await target(service, scripted_model, runs_kit)
    created = await service.client.post("/api/v1/findings", json=finding(agent, run))
    assert created.status_code == 201, created.text
    composer = await service.client.post("/api/v1/agent-composer")
    assert composer.status_code == 200, composer.text
    scripted_model.call("read_finding", {"finding_id": created.json()["id"]}, call_id="read-finding")
    scripted_model.say("Review the suggestion before approving a change.")
    started = await runs_kit.start_thread(service, composer.json(), "Review " + created.json()["id"])
    await (await runs_kit.attempt(service))
    assert (await runs_kit.get_run(service, started["run"]["id"]))["status"] == "completed"
    while not scripted_model.requests.empty():
        request = scripted_model.requests.get_nowait()
    names = {tool["function"]["name"] for tool in request["tools"]}
    assert {"read_finding", "read_trace", "create_agent_revision"} <= names
    assert "submit_finding" not in names and "report_analysis" not in names
    assert finding(agent, run)["suggestion"] in str(request["messages"])
    unchanged = (await service.client.get(f"/api/v1/agents/{agent['id']}")).json()
    assert unchanged["default_revision_id"] == agent["default_revision_id"]


async def test_preset_migration_backfills_existing_composer_and_preserves_other_heads(
    service, scripted_model, runs_kit, database
):
    from a13n_service.distribution import OSS
    from a13n_service.migrations.runner import migration_connection, upgrade
    from alembic import command

    await runs_kit.create_model(service, scripted_model)
    composer = (await service.client.post("/api/v1/agent-composer")).json()
    finder = (await service.client.post("/api/v1/finding-agent")).json()
    with migration_connection(database, OSS) as config:
        command.downgrade(config, "7dc8ec393cc1")
    upgrade(database, OSS)
    restored = (await service.client.get(f"/api/v1/agents/{composer['id']}")).json()
    assert restored["preset_kind"] == "composer" and restored["default_revision_id"] == composer["default_revision_id"]
    preserved = (await service.client.get(f"/api/v1/agents/{finder['id']}")).json()
    assert preserved["source"] == "custom" and preserved["preset_kind"] is None
    assert preserved["default_revision_id"] == finder["default_revision_id"]


async def test_analysis_selection_migration_preserves_order_reads_and_evidence(
    service, scripted_model, runs_kit, database
):
    import json

    from a13n_service.distribution import OSS
    from a13n_service.migrations.runner import migration_connection, upgrade
    from alembic import command
    from sqlalchemy import text

    agent, run = await target(service, scripted_model, runs_kit)
    runtime = replace(service.runtime, traces=Backend(service, run["id"]))
    service.app.state.runtime = runtime
    await service.client.post("/api/v1/finding-agent")
    created = await service.client.post(
        "/api/v1/finding-analyses", json={"trace_id": TRACE}, headers={"Idempotency-Key": "migration-analysis"}
    )
    assert created.status_code == 201, created.text
    result = created.json()
    await analysis.record_read(runtime, actor(service), service.tenant.workspace_id, result["run_id"], TRACE)
    from a13n_service.runs.findings.schemas import FindingCreate
    from a13n_service.runs.findings.service import create_finding

    evidence = await create_finding(
        runtime.storage,
        actor(service),
        service.tenant.workspace_id,
        FindingCreate.model_validate(finding(agent, run)),
        source_run_id=result["run_id"],
    )
    with migration_connection(database, OSS) as config:
        command.downgrade(config, "74b2192ae6ee")
        connection = config.attributes["connection"]
        connection.execute(
            text("""
            UPDATE finding_analyses SET
                trace_ids = CAST(:traces AS jsonb), trace_runs = CAST(:runs AS jsonb),
                reviewed_trace_ids = CAST(:reads AS jsonb), reported = true, limitations = 'Old report'
            WHERE id = :id
        """),
            {
                "id": result["id"],
                "traces": json.dumps(["b" * 32, TRACE]),
                "runs": json.dumps({TRACE: run["id"], "b" * 32: run["id"]}),
                "reads": json.dumps([TRACE]),
            },
        )
        connection.commit()
    upgrade(database, OSS)
    restored = (await service.client.get("/api/v1/finding-analyses")).json()["items"][0]
    assert restored["selected_traces"] == [
        {"trace_id": "b" * 32, "run_id": run["id"]},
        {"trace_id": TRACE, "run_id": run["id"]},
    ]
    assert restored["read_trace_ids"] == [TRACE]
    assert restored["finding_count"] == restored["cited_trace_count"] == 1
    assert restored["session_id"] == result["session_id"] and restored["thread_id"] == result["thread_id"]
    # Coverage claims are deliberately discarded; Findings' evidence and limitations survive.
    finding_id = evidence.id
    preserved = (await service.client.get(f"/api/v1/findings/{finding_id}")).json()
    assert preserved["evidence"] == evidence.model_dump(mode="json")["evidence"]
    assert "reported" not in restored and "limitations" not in restored
