"""Finding provenance, human review, preset separation and bounded ordinary-run analysis."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest
from a13n_service.api_tools import tool_name
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.platform import PLATFORM_GROUPS, PLATFORM_TOOLS
from a13n_service.infra.telemetry import correlation_attributes
from a13n_service.providers.traces import Span, SpanPage
from a13n_service.runs.findings import analysis, preset
from a13n_service.runs.findings.schemas import Category
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal

pytestmark = pytest.mark.anyio
TRACE = "a" * 32


def operation(service, method, path):
    """The platform tool of an API operation."""
    return tool_name(service.app.openapi()["paths"][f"/api/v1{path}"][method]["operationId"])


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
        "category": "answer_quality",
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


async def test_categories_validate_persist_filter_and_bind_pagination(service, scripted_model, runs_kit):
    from a13n_service.infra.db import short_session
    from a13n_service.runs.findings.tables import FindingRow
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError

    agent, run = await target(service, scripted_model, runs_kit)
    body = finding(agent, run)
    for category in get_args(Category.__value__):
        response = await service.client.post(
            "/api/v1/findings", json={**body, "category": category, "source_key": category}
        )
        assert response.status_code == 201, response.text
        assert response.json()["category"] == category
    for invalid in ("execution", "other", "Tool execution", ""):
        response = await service.client.post(
            "/api/v1/findings", json={**body, "category": invalid, "source_key": "invalid"}
        )
        assert response.status_code == 400, response.text
        assert (await service.client.get("/api/v1/findings", params={"category": invalid})).status_code == 400
    second = await service.client.post(
        "/api/v1/findings", json={**body, "category": "tool_execution", "source_key": "second-execution"}
    )
    assert second.status_code == 201
    query = {"category": "tool_execution", "assessment": "unreviewed", "limit": 1}
    first = (await service.client.get("/api/v1/findings", params=query)).json()
    assert first["items"][0]["id"] == second.json()["id"] and first["next_cursor"]
    page = await service.client.get("/api/v1/findings", params={**query, "cursor": first["next_cursor"]})
    assert page.status_code == 200 and len(page.json()["items"]) == 1
    assert page.json()["items"][0]["category"] == "tool_execution"
    changed = await service.client.get(
        "/api/v1/findings", params={**query, "category": "answer_quality", "cursor": first["next_cursor"]}
    )
    assert changed.status_code == 400
    with pytest.raises(IntegrityError):
        async with short_session(service.runtime.storage) as session:
            await session.execute(
                update(FindingRow).where(FindingRow.id == second.json()["id"]).values(category="other")
            )
            await session.commit()


async def test_presets_coexist_and_finder_writes_only_findings(service, scripted_model, runs_kit):
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
    config = revision.json()["config"]
    operations = {tool.key: (tool.method, tool.path) for tool in PLATFORM_TOOLS}
    selected = {
        operations[key]: tool["permission"]
        for key, tool in config["toolsets"]["execution"]["tools"].items()
        if tool["enabled"]
    }
    assert selected == dict.fromkeys(preset.TOOLS, "allow")
    others = ("files", "shell", "memory", *(group for group in PLATFORM_GROUPS if group != "execution"))
    assert not any(config["toolsets"][key]["enabled"] for key in others)
    # Submitting a finding is the one write of the execution group.
    writes = [
        (method, path)
        for path, operations in service.app.openapi()["paths"].items()
        for method, item in operations.items()
        if isinstance(item, dict) and item.get("x-a13n-platform") == {"group": "execution", "effect": "write"}
    ]
    assert writes == [("post", "/api/v1/findings")]
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
    read_trace, submit = operation(service, "get", "/traces/{trace_id}"), operation(service, "post", "/findings")
    # The analysis Run's own credential reads only its selected traces.
    scripted_model.call(read_trace, {"trace_id": "c" * 32}, call_id="unselected")
    scripted_model.call(read_trace, {"trace_id": TRACE}, call_id="read-1")
    submitted = finding(agent, run)
    submitted["source_key"] = result["id"] + ":execution"
    # Invalid categories produce ordinary tool validation feedback; a corrected call can succeed.
    scripted_model.call(submit, {"request_body": {**submitted, "category": "other"}}, call_id="bad-category")
    scripted_model.call(submit, {"request_body": submitted}, call_id="submit-1")
    # Two distinct diagnoses citing one trace count as two findings and one cited trace.
    scripted_model.call(
        submit, {"request_body": {**submitted, "source_key": result["id"] + ":answer"}}, call_id="submit-2"
    )
    scripted_model.say("One unconfirmed issue reported.")
    async with runs_kit.platform_runtime(service, runtime) as served:
        await (await runs_kit.attempt(service, runtime=served))
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
    tools = {tool["function"]["name"]: tool["function"] for tool in last_request["tools"]}
    operations = {tool.key: (tool.method, tool.path) for tool in PLATFORM_TOOLS}
    assert {operations[name] for name in tools if name in operations} == preset.TOOLS
    assert all(f'"{category}"' in json.dumps(tools[submit]["parameters"]) for category in get_args(Category.__value__))
    results = {
        message["tool_call_id"]: message["content"] for message in last_request["messages"] if message["role"] == "tool"
    }
    assert "outside the analysis selection" in results["unselected"]
    assert "category" in results["bad-category"]


async def test_analysis_credential_lists_no_traces_and_reads_only_selected_ones(service, scripted_model, runs_kit):
    from a13n_service.runs.claim import claim
    from a13n_service.runs.platform_tokens import issue

    from .mcp_support import call, client

    _, run = await target(service, scripted_model, runs_kit)
    service.app.state.runtime = replace(service.runtime, traces=Backend(service, run["id"]))
    await service.client.post("/api/v1/finding-agent")
    created = await service.client.post(
        "/api/v1/finding-analyses", json={"trace_id": TRACE}, headers={"Idempotency-Key": "credential"}
    )
    assert created.status_code == 201, created.text
    (lease,) = await claim(service.runtime, worker_id="finding-test", worker_build="test", limit=1)
    assert lease.run_id == created.json()["run_id"]
    # A Host that offered more tools than the Finding Agent selects would still meet these route rules.
    token = await issue(service.runtime, lease, frozenset({"execution"}))
    async with client(service, {"secret": token}) as http:
        listed = await call(http, service, "GET", "/traces")
        assert "an analysis reads its selected traces by ID" in listed["body"]["error"]["message"]
        unselected = await call(http, service, "GET", "/traces/{trace_id}", trace_id="c" * 32)
        assert "outside the analysis selection" in unselected["body"]["error"]["message"]
        unselected_span = await call(
            http, service, "GET", "/traces/{trace_id}/spans/{span_id}", trace_id="c" * 32, span_id="b" * 16
        )
        assert "outside the analysis selection" in unselected_span["body"]["error"]["message"]
        selected = await call(
            http, service, "GET", "/traces/{trace_id}/spans/{span_id}", trace_id=TRACE, span_id="b" * 16
        )
        assert selected["status"] == 200, selected
    status = (await service.client.get("/api/v1/finding-analyses")).json()["items"][0]
    assert status["read_trace_ids"] == [TRACE]


async def test_workspace_analysis_reviews_multiple_agents_and_excludes_finder_runs(service, scripted_model, runs_kit):
    first, first_run = await target(service, scripted_model, runs_kit)
    second = await runs_kit.add_agent(service, "Second", "scripted")
    scripted_model.say("Done")
    second_run = (await runs_kit.start_thread(service, second, "another task"))["run"]
    await (await runs_kit.attempt(service))
    finder = (await service.client.post("/api/v1/finding-agent")).json()
    scripted_model.say("No analysis requested")
    finder_run = (await runs_kit.start_thread(service, finder, "hello"))["run"]
    async with runs_kit.platform_runtime(service) as served:
        await (await runs_kit.attempt(service, runtime=served))

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
    navigation = navigation_in(await analysis_input(service, result["run_id"]))
    assert set(navigation) == {TRACE, "b" * 32}
    console = f"{runtime.settings.server.public_url}/workspace/{service.tenant.workspace_id}"
    for trace_id, agent, run in ((TRACE, first, first_run), ("b" * 32, second, second_run)):
        assert navigation[trace_id] == {
            "agent_id": agent["id"],
            "agent_revision_id": agent["default_revision_id"],
            "agent_name": agent["name"],
            "agent_url": f"{console}/agents/{agent['id']}",
            "run_url": f"{console}/sessions/{run['session_id']}/threads/{run['thread_id']}/runs/{run['id']}?view=chat",
            "trace_url": f"{console}/traces/{trace_id}",
        }
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
        scripted_model.call(
            operation(service, "get", "/traces/{trace_id}"), {"trace_id": trace_id}, call_id=f"r{index}"
        )
        submitted = finding(agent, run)
        submitted["evidence"][0]["trace_id"] = trace_id
        submitted["source_key"] = result["id"] + f":{index}"
        scripted_model.call(operation(service, "post", "/findings"), {"request_body": submitted}, call_id=f"s{index}")
    scripted_model.say("Two issues reported")
    async with runs_kit.platform_runtime(service, runtime) as served:
        await (await runs_kit.attempt(service, runtime=served))
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
    assert set(navigation_in(await analysis_input(service, capped.json()["run_id"]))) == {TRACE}


async def test_analysis_needs_a_trace_backend(service, scripted_model, runs_kit):
    agent, _ = await target(service, scripted_model, runs_kit)
    await service.client.post("/api/v1/finding-agent")
    unavailable = await service.client.post(
        "/api/v1/finding-analyses", json={"agent_id": agent["id"]}, headers={"Idempotency-Key": "missing-backend"}
    )
    assert unavailable.status_code == 503


async def test_composer_reads_durable_finding_without_changing_the_target(service, scripted_model, runs_kit):
    agent, run = await target(service, scripted_model, runs_kit)
    created = await service.client.post("/api/v1/findings", json=finding(agent, run))
    assert created.status_code == 201, created.text
    composer = await service.client.post("/api/v1/agent-composer")
    assert composer.status_code == 200, composer.text
    paths = service.app.openapi()["paths"]
    read_finding = tool_name(paths["/api/v1/findings/{finding_id}"]["get"]["operationId"])
    scripted_model.call(read_finding, {"finding_id": created.json()["id"]}, call_id="read-finding")
    scripted_model.say("Review the suggestion before approving a change.")
    started = await runs_kit.start_thread(service, composer.json(), "Review " + created.json()["id"])
    async with runs_kit.platform_runtime(service) as served:
        await (await runs_kit.attempt(service, runtime=served))
    assert (await runs_kit.get_run(service, started["run"]["id"]))["status"] == "completed"
    while not scripted_model.requests.empty():
        request = scripted_model.requests.get_nowait()
    names = {tool["function"]["name"] for tool in request["tools"]}
    assert {
        read_finding,
        tool_name(paths["/api/v1/agents/{agent_id}/revisions"]["post"]["operationId"]),
        "view",
    } <= names
    assert finding(agent, run)["suggestion"] in str(request["messages"])
    unchanged = (await service.client.get(f"/api/v1/agents/{agent['id']}")).json()
    assert unchanged["default_revision_id"] == agent["default_revision_id"]


async def test_final_findings_migration_backfills_composer_and_preserves_other_heads(
    service, scripted_model, runs_kit, database
):
    from a13n_service.distribution import OSS
    from a13n_service.migrations.runner import migration_connection, upgrade
    from alembic import command
    from alembic.script import ScriptDirectory

    await runs_kit.create_model(service, scripted_model)
    composer = (await service.client.post("/api/v1/agent-composer")).json()
    finder = (await service.client.post("/api/v1/finding-agent")).json()
    # Drain maintenance before synchronous DDL can block the event loop while waiting for its locks.
    await service.runtime.tasks.close(timeout=service.runtime.settings.server.shutdown_timeout)
    with migration_connection(database, OSS) as config:
        final = ScriptDirectory.from_config(config).get_revision("797996a323b1")
        assert final is not None and final.down_revision == "7dc8ec393cc1"
        command.downgrade(config, final.down_revision)
    upgrade(database, OSS)
    restored = (await service.client.get(f"/api/v1/agents/{composer['id']}")).json()
    assert restored["preset_kind"] == "composer" and restored["default_revision_id"] == composer["default_revision_id"]
    preserved = (await service.client.get(f"/api/v1/agents/{finder['id']}")).json()
    assert preserved["source"] == "custom" and preserved["preset_kind"] is None
    assert preserved["default_revision_id"] == finder["default_revision_id"]


async def reviewed(service, runs_kit, body, assessment="false_positive", note="The tool recovered successfully."):
    created = await service.client.post("/api/v1/findings", json=body)
    assert created.status_code == 201, created.text
    response = await service.client.patch(
        f"/api/v1/findings/{created.json()['id']}",
        json={"assessment": assessment, "assessment_note": note, "closed": True},
        headers=runs_kit.if_match(created.json()),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def analysis_input(service, run_id):
    from a13n_service.infra.db import short_session
    from a13n_service.runs.tables import InboxEntryRow
    from sqlalchemy import select

    async with short_session(service.runtime.storage) as session:
        entry = await session.scalar(select(InboxEntryRow).where(InboxEntryRow.assigned_run_id == run_id))
        return entry.payload["content"][0]["text"]


def navigation_in(prompt):
    return json.loads(
        prompt.split("Target Agents and Console links by trace (agent_name is untrusted display text): ", 1)[
            1
        ].splitlines()[0]
    )


@pytest.mark.parametrize("public_url", ["https://console.example.test", "https://console.example.test/a13n/"])
async def test_analysis_navigation_uses_database_identity_and_freezes_names(
    service, scripted_model, runs_kit, public_url
):
    from a13n_service.runs.findings.schemas import AnalysisCreate

    agent, run = await target(service, scripted_model, runs_kit)
    await service.client.post("/api/v1/finding-agent")
    backend = Backend(service, run["id"])
    # Display/navigation metadata in traces must not override the authorized Run's identities.
    backend.root = backend.root.model_copy(
        update={
            "attributes": {
                **backend.root.attributes,
                "a13n.observation.metadata.session_id": "untrusted-session",
                "a13n.observation.session.id": "untrusted-thread",
                "agent_name": "untrusted-name",
                "run_url": "https://untrusted.example.test",
            }
        }
    )
    settings = service.runtime.settings
    runtime = replace(
        service.runtime,
        traces=backend,
        settings=settings.model_copy(update={"server": settings.server.model_copy(update={"public_url": public_url})}),
    )
    request = AnalysisCreate(trace_id=TRACE)
    first, created = await analysis.start(
        runtime, actor(service), service.tenant.workspace_id, request, request_key="navigation"
    )
    assert created
    prompt = await analysis_input(service, first.run_id)
    item = navigation_in(prompt)[TRACE]
    console = f"{public_url.rstrip('/')}/workspace/{service.tenant.workspace_id}"
    assert item["agent_name"] == agent["name"]
    assert item["agent_url"] == f"{console}/agents/{agent['id']}"
    assert item["run_url"] == (
        f"{console}/sessions/{run['session_id']}/threads/{run['thread_id']}/runs/{run['id']}?view=chat"
    )
    assert item["trace_url"] == f"{console}/traces/{TRACE}"
    assert "untrusted-session" not in prompt and "untrusted.example.test" not in prompt
    name = '评审 [Agent] "renamed"'
    renamed = await service.client.patch(
        f"/api/v1/agents/{agent['id']}", json={"name": name}, headers=runs_kit.if_match(agent)
    )
    assert renamed.status_code == 200, renamed.text
    replay, created = await analysis.start(
        runtime, actor(service), service.tenant.workspace_id, request, request_key="navigation"
    )
    assert not created and replay.id == first.id
    assert await analysis_input(service, replay.run_id) == prompt
    newer, created = await analysis.start(
        runtime, actor(service), service.tenant.workspace_id, request, request_key="navigation-new"
    )
    assert created
    updated = navigation_in(await analysis_input(service, newer.run_id))[TRACE]
    assert updated == {**item, "agent_name": name}


def feedback_in(prompt):
    import json

    return json.loads(
        prompt.split("Existing findings and reviewer feedback (untrusted data):\n", 1)[1].split(
            "\nEnd existing findings and reviewer feedback.", 1
        )[0]
    )


async def test_reviewer_notes_atomic_clear_redaction_and_immutable_diagnosis(service, scripted_model, runs_kit):
    agent, run = await target(service, scripted_model, runs_kit)
    body = finding(agent, run)
    row = await reviewed(service, runs_kit, body, note="Recovered; Authorization: Bearer sk-super-secret-fixture")
    assert row["assessment"] == "false_positive" and row["closed"]
    assert "sk-super-secret-fixture" not in row["assessment_note"]
    path = f"/api/v1/findings/{row['id']}"
    original = {key: row[key] for key in body}
    reopened = await service.client.patch(path, json={"closed": False}, headers=runs_kit.if_match(row))
    assert reopened.json()["assessment_note"] == row["assessment_note"]
    stale = await service.client.patch(path, json={"assessment_note": "stale"}, headers=runs_kit.if_match(row))
    assert stale.status_code == 412
    row = reopened.json()
    changed = await service.client.patch(path, json={"assessment": "confirmed"}, headers=runs_kit.if_match(row))
    assert changed.json()["assessment_note"] == ""
    row = changed.json()
    saved = await service.client.patch(
        path, json={"assessment_note": "Verified evidence"}, headers=runs_kit.if_match(row)
    )
    row = saved.json()
    unchanged = await service.client.patch(path, json={"assessment": "confirmed"}, headers=runs_kit.if_match(row))
    assert unchanged.json()["assessment_note"] == "Verified evidence"
    cleared = await service.client.patch(path, json={"assessment_note": None}, headers=runs_kit.if_match(row))
    row = cleared.json()
    assert row["assessment_note"] == ""
    assert {key: row[key] for key in body} == original
    for bad in ({"assessment": None}, {"closed": None}, {"assessment_note": "x" * 2049}, {"explanation": "overwrite"}):
        response = await service.client.patch(path, json=bad, headers=runs_kit.if_match(row))
        assert response.status_code == 400, response.text


async def test_feedback_input_is_frozen_on_retry_and_scripted_diagnosis_uses_correction(
    service, scripted_model, runs_kit
):
    agent, run = await target(service, scripted_model, runs_kit)
    row = await reviewed(service, runs_kit, finding(agent, run))
    runtime = replace(service.runtime, traces=Backend(service, run["id"]))
    service.app.state.runtime = runtime
    await service.client.post("/api/v1/finding-agent")
    request = {"trace_id": TRACE}
    header = {"Idempotency-Key": "feedback-1"}
    first = await service.client.post("/api/v1/finding-analyses", json=request, headers=header)
    assert first.status_code == 201, first.text
    prompt = await analysis_input(service, first.json()["run_id"])
    feedback = feedback_in(prompt)
    assert [item["id"] for item in feedback["items"]] == [row["id"]]
    item = feedback["items"][0]
    assert item["assessment"] == "false_positive" and item["assessment_note"] == row["assessment_note"]
    assert item["diagnosis"]["explanation"] == row["explanation"]
    assert item["version"] == row["version"] and not feedback["truncated"]
    changed = await service.client.patch(
        f"/api/v1/findings/{row['id']}",
        json={"assessment": "confirmed", "assessment_note": "New evidence"},
        headers=runs_kit.if_match(row),
    )
    assert changed.status_code == 200
    replay = await service.client.post("/api/v1/finding-analyses", json=request, headers=header)
    assert replay.status_code == 200 and replay.json()["id"] == first.json()["id"]
    assert await analysis_input(service, first.json()["run_id"]) == prompt
    scripted_model.call(operation(service, "get", "/traces/{trace_id}"), {"trace_id": TRACE}, call_id="read")
    scripted_model.say(
        "The previous completion diagnosis was disproven: successful recovery means no issue is established."
    )
    async with runs_kit.platform_runtime(service, runtime) as served:
        await (await runs_kit.attempt(service, runtime=served))
    while not scripted_model.requests.empty():
        model_request = scripted_model.requests.get_nowait()
    assert row["assessment_note"] in str(model_request["messages"])
    assert "permanent category suppression" in str(model_request["messages"])
    assert (await service.client.get("/api/v1/findings", params={"closed": "false"})).json()["items"] == []
    newer = await service.client.post(
        "/api/v1/finding-analyses", json=request, headers={"Idempotency-Key": "feedback-2"}
    )
    assert newer.status_code == 201
    latest = feedback_in(await analysis_input(service, newer.json()["run_id"]))["items"][0]
    assert latest["assessment"] == "confirmed" and latest["assessment_note"] == "New evidence"
    assert latest["version"] == changed.json()["version"]


async def test_feedback_exact_revision_workspace_isolation_and_total_budgets(service, scripted_model, runs_kit):
    from a13n_service.runs.findings import service as findings
    from a13n_service.runs.schemas import canonical_json

    first, first_run = await target(service, scripted_model, runs_kit)
    second = await runs_kit.add_agent(service, "Second", "scripted")
    scripted_model.say("Done")
    second_run = (await runs_kit.start_thread(service, second, "second request"))["run"]
    await (await runs_kit.attempt(service))
    for index in range(24):
        agent, run = (first, first_run) if index % 2 else (second, second_run)
        await reviewed(service, runs_kit, {**finding(agent, run), "source_key": f"feedback-{index}"})
    unreviewed = await service.client.post(
        "/api/v1/findings", json={**finding(first, first_run), "source_key": "unreviewed"}
    )
    pairs = {(first["id"], first["default_revision_id"]), (second["id"], second["default_revision_id"])}
    context = await findings.analysis_context(
        service.runtime.storage, actor(service), service.tenant.workspace_id, pairs
    )
    assert len(context["items"]) <= findings.CONTEXT_ITEMS and context["truncated"]
    assert len(canonical_json(context)) <= findings.CONTEXT_BYTES
    prior = next(item for item in context["items"] if item["id"] == unreviewed.json()["id"])
    assert prior["assessment"] == "unreviewed" and prior["assessment_note"] == "" and not prior["closed"]
    assert prior["diagnosis"]["explanation"] == unreviewed.json()["explanation"]
    assert {item["agent_id"] for item in context["items"]} == {first["id"], second["id"]}
    assert context == await findings.analysis_context(
        service.runtime.storage, actor(service), service.tenant.workspace_id, pairs
    )
    # A real later revision of the same Agent does not inherit earlier judgments.
    head = await service.client.get(f"/api/v1/agents/{first['id']}")
    old_revision = (
        await service.client.get(f"/api/v1/agents/{first['id']}/revisions/{first['default_revision_id']}")
    ).json()
    changed = await service.client.post(
        f"/api/v1/agents/{first['id']}/revisions",
        json={"config": {**old_revision["config"], "instructions": "A revised instruction"}},
        headers={"If-Match": head.headers["etag"]},
    )
    assert changed.status_code == 201, changed.text
    new_revision_id = changed.json()["id"]
    different_revision = await findings.analysis_context(
        service.runtime.storage, actor(service), service.tenant.workspace_id, {(first["id"], new_revision_id)}
    )
    assert different_revision["items"] == []
    scripted_model.say("Done")
    new_run = (await runs_kit.start_thread(service, first, "new revision", agent_revision_id=new_revision_id))["run"]
    await (await runs_kit.attempt(service))
    newer = await reviewed(
        service,
        runs_kit,
        {**finding({**first, "default_revision_id": new_revision_id}, new_run), "source_key": "new-revision"},
    )
    new_context = await findings.analysis_context(
        service.runtime.storage, actor(service), service.tenant.workspace_id, {(first["id"], new_revision_id)}
    )
    assert [item["id"] for item in new_context["items"]] == [newer["id"]]
    old_context = await findings.analysis_context(
        service.runtime.storage, actor(service), service.tenant.workspace_id, pairs
    )
    assert newer["id"] not in {item["id"] for item in old_context["items"]}
    other = await service.client.post(
        f"/api/v1/organizations/{service.tenant.organization_id}/workspaces", json={"name": "Other"}
    )
    hidden = await findings.analysis_context(service.runtime.storage, actor(service), other.json()["id"], pairs)
    assert hidden["items"] == []
    # A long note is explicitly shortened, with a global serialized byte budget.
    await reviewed(service, runs_kit, {**finding(first, first_run), "source_key": "long-note"}, note="界" * 2048)
    bounded = await findings.analysis_context(
        service.runtime.storage, actor(service), service.tenant.workspace_id, pairs
    )
    assert bounded["items"][0]["assessment_note"] == "界" * 1024
    assert "assessment_note" in bounded["items"][0]["truncated_fields"]
    assert bounded["truncated"] and len(canonical_json(bounded)) <= findings.CONTEXT_BYTES


async def test_error_priority_cross_page_selection_and_feedback_from_final_cap(service, scripted_model, runs_kit):
    first, first_run = await target(service, scripted_model, runs_kit)
    second = await runs_kit.add_agent(service, "Second", "scripted")
    scripted_model.say("Done")
    second_run = (await runs_kit.start_thread(service, second, "later error"))["run"]
    await (await runs_kit.attempt(service))
    first_feedback = await reviewed(service, runs_kit, finding(first, first_run))
    second_feedback = await reviewed(service, runs_kit, {**finding(second, second_run), "source_key": "second"})

    class PriorityBackend(Backend):
        async def query(self, query):
            self.requests.append(query)
            if query.trace_id:
                return SpanPage(items=[self.root], next_cursor=None)
            root = self.root.model_copy(update={"status": "ok", "level": "info"})
            if query.cursor:
                root = self.root.model_copy(
                    update={
                        "trace_id": "b" * 32,
                        "attributes": correlation_attributes(
                            service.tenant.organization_id, service.tenant.workspace_id, run_id=second_run["id"]
                        ),
                    }
                )
            return SpanPage(items=[root], next_cursor=None if query.cursor else "next")

    runtime = replace(service.runtime, traces=PriorityBackend(service, first_run["id"]))
    service.app.state.runtime = runtime
    await service.client.post("/api/v1/finding-agent")
    response = await service.client.post(
        "/api/v1/finding-analyses", json={"max_traces": 1}, headers={"Idempotency-Key": "priority"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["selected_traces"] == [{"trace_id": "b" * 32, "run_id": second_run["id"]}]
    context = feedback_in(await analysis_input(service, response.json()["run_id"]))
    assert [item["id"] for item in context["items"]] == [second_feedback["id"]]
    assert first_feedback["id"] not in str(context)
    answer_only = await service.client.post(
        "/api/v1/finding-analyses",
        json={"max_traces": 1, "presets": ["answer"]},
        headers={"Idempotency-Key": "answer-priority"},
    )
    assert answer_only.json()["selected_traces"] == [{"trace_id": TRACE, "run_id": first_run["id"]}]


async def test_unreviewed_diagnosis_is_context_not_confirmation_and_retry_keeps_values(
    service, scripted_model, runs_kit
):
    agent, run = await target(service, scripted_model, runs_kit)
    previous = await service.client.post("/api/v1/findings", json=finding(agent, run))
    assert previous.status_code == 201, previous.text
    row = previous.json()
    runtime = replace(service.runtime, traces=Backend(service, run["id"]))
    service.app.state.runtime = runtime
    await service.client.post("/api/v1/finding-agent")
    body, headers = {"trace_id": TRACE}, {"Idempotency-Key": "existing-unreviewed"}
    accepted = await service.client.post("/api/v1/finding-analyses", json=body, headers=headers)
    assert accepted.status_code == 201, accepted.text
    prompt = await analysis_input(service, accepted.json()["run_id"])
    item = feedback_in(prompt)["items"][0]
    assert item["id"] == row["id"] and item["assessment"] == "unreviewed"
    assert item["diagnosis"]["explanation"] == row["explanation"]
    assert not item["closed"] and item["assessment_note"] == ""
    closed = await service.client.patch(
        f"/api/v1/findings/{row['id']}", json={"closed": True}, headers=runs_kit.if_match(row)
    )
    assert closed.status_code == 200, closed.text
    retried = await service.client.post("/api/v1/finding-analyses", json=body, headers=headers)
    assert retried.status_code == 200
    assert await analysis_input(service, retried.json()["run_id"]) == prompt
    scripted_model.call(operation(service, "get", "/traces/{trace_id}"), {"trace_id": TRACE}, call_id="read")
    scripted_model.say(
        f"Equivalent evidence is already recorded in {row['id']}; it remains unreviewed. No new finding."
    )
    async with runs_kit.platform_runtime(service, runtime) as served:
        await (await runs_kit.attempt(service, runtime=served))
    while not scripted_model.requests.empty():
        request = scripted_model.requests.get_nowait()
    messages = str(request["messages"])
    assert row["id"] in messages
    assert "not human confirmation" in messages and "instead of submitting it again" in messages
    stored = (await service.client.get("/api/v1/findings")).json()["items"]
    assert [entry["id"] for entry in stored] == [row["id"]]
    assert stored[0]["assessment"] == "unreviewed" and stored[0]["closed"]
    new = await service.client.post(
        "/api/v1/finding-analyses", json=body, headers={"Idempotency-Key": "existing-closed"}
    )
    assert new.status_code == 201, new.text
    current = feedback_in(await analysis_input(service, new.json()["run_id"]))["items"][0]
    assert current["id"] == row["id"] and current["closed"] and current["assessment"] == "unreviewed"


async def test_findings_mcp_reads_preserve_review_filters_history_and_workspace_confinement(
    service, scripted_model, runs_kit
):
    from a13n_service.api_tools import tool_name

    from .mcp_support import call, client, key, rpc

    agent, run = await target(service, scripted_model, runs_kit)
    row = await reviewed(service, runs_kit, finding(agent, run))
    runtime = replace(service.runtime, traces=Backend(service, run["id"]))
    service.app.state.runtime = runtime
    await service.client.post("/api/v1/finding-agent")
    result = await service.client.post(
        "/api/v1/finding-analyses", json={"trace_id": TRACE}, headers={"Idempotency-Key": "mcp-history"}
    )
    assert result.status_code == 201, result.text
    credential = await key(service)
    async with client(service, credential) as http:
        names = {item["name"] for item in (await rpc(http, "tools/list")).json()["result"]["tools"]}
        operations = service.app.openapi()["paths"]
        assert tool_name(operations["/api/v1/findings"]["get"]["operationId"]) in names
        assert tool_name(operations["/api/v1/findings"]["post"]["operationId"]) in names
        listed = await call(http, service, "GET", "/findings", assessment="false_positive", closed=True, limit=1)
        assert [item["id"] for item in listed["body"]["items"]] == [row["id"]]
        read = await call(http, service, "GET", "/findings/{finding_id}", finding_id=row["id"])
        assert read["body"]["assessment_note"] == row["assessment_note"]
        assert read["headers"]["etag"] == f'"{row["id"]}:{row["version"]}"'
        history = await call(http, service, "GET", "/finding-analyses", limit=1)
        assert history["body"]["items"][0]["id"] == result.json()["id"]
        assert history["body"]["items"][0]["selected_traces"] == result.json()["selected_traces"]
    other = await service.client.post(f"{service.organization}/workspaces", json={"name": "MCP other"})
    async with client(service, await key(service, other.json()["id"])) as http:
        hidden = await call(http, service, "GET", "/findings/{finding_id}", finding_id=row["id"])
        assert hidden["status"] == 404
        empty = await call(http, service, "GET", "/finding-analyses")
        assert empty["body"]["items"] == []
