"""Run-scoped MCP operations keep their authority through discovery, HTTP and recovery."""

import json
from dataclasses import replace

import pytest
from a13n_service.api_tools import tool_name
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.agents.composer import configuration
from a13n_service.runs.claim import claim
from a13n_service.runs.platform_tokens import issue
from a13n_service.runs.seal import LeaseExpirer
from a13n_service.runs.tables import AttemptRow, RunRow
from a13n_service.tenancy.access import reauthenticate
from a13n_service.tenancy.authorize import WorkspaceScope, allowed_verbs
from a13n_service.tenancy.tables import GrantRow, PrincipalRow, TokenRow, WorkspaceRow
from sqlalchemy import delete, select, update
from starlette.requests import Request
from starlette.responses import Response

from .mcp_support import call, client, rpc
from .test_providers import models_endpoint

pytestmark = pytest.mark.anyio


async def set_role(session, service, role):
    await session.execute(delete(GrantRow).where(GrantRow.principal_id == service.tenant.principal_id))
    session.add(
        GrantRow(
            id=new_object_id("gr"),
            organization_id=service.tenant.organization_id,
            workspace_id=None,
            principal_id=service.tenant.principal_id,
            created_by_id=service.tenant.principal_id,
            role=role,
        )
    )


async def lease_for(service, scripted_model, runs_kit):
    agent = await runs_kit.create_agent(service, scripted_model)
    submitted = await runs_kit.start_thread(service, agent, "configure resources")
    (lease,) = await claim(service.runtime, worker_id="platform-test", worker_build="test", limit=1)
    return lease, agent, submitted


async def authenticate_token(service, token):
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/agents",
            "headers": [(b"authorization", ("Bearer " + token).encode())],
            "app": service.app,
        }
    )
    credential = await service.runtime.access.authenticator.authenticate(request, Response())
    assert credential is not None
    return credential


async def test_run_mcp_discovery_http_scope_versions_and_audit(service, scripted_model, runs_kit):
    lease, agent, _ = await lease_for(service, scripted_model, runs_kit)
    token = await issue(service.runtime, lease, frozenset({"agents", "connections", "execution"}))
    async with client(service, {"secret": token}) as http:
        response = await rpc(http, "tools/list")
        assert response.status_code == 200, response.text
        tools = response.json()["result"]["tools"]
        assert tools and all(
            tool["_meta"]["x-a13n-platform"]["group"] in {"agents", "connections", "execution"} for tool in tools
        )
        names = {tool["name"] for tool in tools}
        paths = service.app.openapi()["paths"]
        for suffix in ("/apps", "/apps/{app}", "/apps/{app}/actions"):
            assert tool_name(paths["/api/v1/connector-providers/{provider_id}" + suffix]["get"]["operationId"]) in names
        assert tool_name(paths["/api/v1/models"]["get"]["operationId"]) not in names
        assert "search_documents" not in {tool["name"] for tool in tools}
        assert (await rpc(http, "tools/call", name="search_documents", arguments={"query": "agents"})).json()["result"][
            "isError"
        ]
        for path in ("/api/v1/memories", "/api/v1/users/me", "/api/v1/openapi.json", "/healthz"):
            assert (await http.get(path)).status_code == 403
        assert (await http.post(f"/api/v1/runs/{lease.run_id}/interrupt")).status_code == 403
        http.headers["x-workspace-id"] = "ws_another"
        assert (await rpc(http, "tools/list")).status_code == 403
        del http.headers["x-workspace-id"]
        read = await call(http, service, "GET", "/agents/{agent_id}", agent_id=agent["id"])
        etag = read["headers"]["etag"]
        changed = await call(
            http,
            service,
            "PATCH",
            "/agents/{agent_id}",
            agent_id=agent["id"],
            **{"If-Match": etag},
            request_body={"description": "from a run"},
        )
        assert changed["status"] == 200, changed
        stale = await call(
            http,
            service,
            "PATCH",
            "/agents/{agent_id}",
            agent_id=agent["id"],
            **{"If-Match": etag},
            request_body={"description": "stale"},
        )
        assert stale["status"] == 412
        listed = await call(http, service, "GET", "/runs", agent_id=agent["id"], status="running", limit=1)
        assert [item["id"] for item in listed["body"]["items"]] == [lease.run_id]
        detail = await call(
            http,
            service,
            "GET",
            "/runs/{run_id}/attempts/{attempt_id}",
            run_id=lease.run_id,
            attempt_id=lease.attempt_id,
        )
        assert detail["status"] == 200 and "lease_token_hash" not in detail["body"]
    async with transaction(service.runtime.storage) as session:
        event = await session.scalar(
            select(AuditEventRow).where(AuditEventRow.target_id == agent["id"], AuditEventRow.action == "agent.update")
        )
        assert event.actor_id == service.tenant.principal_id
        assert event.details["execution_origin"]["run_id"] == lease.run_id
        assert event.details["execution_origin"]["attempt_id"] == lease.attempt_id
        assert event.details["execution_origin"]["request_id"]


async def test_run_provider_test_discovery_and_group_scope(service, scripted_model, runs_kit):
    lease, _, _ = await lease_for(service, scripted_model, runs_kit)
    token = await issue(service.runtime, lease, frozenset({"providers"}))
    paths = service.app.openapi()["paths"]
    async with client(service, {"secret": token}) as http:
        discovered = (await rpc(http, "tools/list")).json()["result"]["tools"]
        tools = {tool["name"]: tool for tool in discovered}
        for kind in ("model", "environment", "connector", "web"):
            operation = paths[f"/api/v1/{kind}-providers/{{provider_id}}/test"]["post"]
            tool = tools[tool_name(operation["operationId"])]
            assert tool["_meta"]["x-a13n-platform"] == {"group": "providers", "effect": "write"}
            assert tool["annotations"]["readOnlyHint"] is False
            assert set(tool["inputSchema"]["properties"]) == {"provider_id"}
            assert tool["inputSchema"]["required"] == ["provider_id"]
        memory = paths["/api/v1/memory-providers/{provider_id}/test"]["post"]
        assert tool_name(memory["operationId"]) not in tools
        rejected = await rpc(
            http, "tools/call", name=tool_name(memory["operationId"]), arguments={"provider_id": "mp_hidden"}
        )
        assert rejected.json()["result"]["isError"]
        assert (await http.post("/api/v1/memory-providers/mp_hidden/test")).status_code == 403

    token = await issue(service.runtime, lease, frozenset({"models"}))
    operation = paths["/api/v1/model-providers/{provider_id}/test"]["post"]
    async with client(service, {"secret": token}) as http:
        discovered = (await rpc(http, "tools/list")).json()["result"]["tools"]
        assert tool_name(operation["operationId"]) not in {tool["name"] for tool in discovered}
        rejected = await rpc(
            http, "tools/call", name=tool_name(operation["operationId"]), arguments={"provider_id": "mp_hidden"}
        )
        assert rejected.json()["result"]["isError"]
        assert (await http.post("/api/v1/model-providers/mp_hidden/test")).status_code == 403


@pytest.mark.parametrize("change", ["cancel", "expire", "revoke", "tamper", "disable", "archive"])
async def test_run_token_fails_closed_after_execution_or_authority_changes(service, scripted_model, runs_kit, change):
    lease, _, _ = await lease_for(service, scripted_model, runs_kit)
    token = await issue(service.runtime, lease, frozenset({"agents"}))
    credential = await authenticate_token(service, token)
    async with client(service, {"secret": token}) as http:
        assert (await rpc(http, "tools/list")).status_code == 200
        async with transaction(service.runtime.storage) as session:
            if change == "cancel":
                run = await session.get_one(RunRow, lease.run_id)
                run.cancel_requested_at = run.created_at
            elif change == "expire":
                await session.execute(
                    update(AttemptRow)
                    .where(AttemptRow.id == lease.attempt_id)
                    .values(lease_expires_at=AttemptRow.created_at)
                )
            elif change == "revoke":
                await set_role(session, service, "viewer")
            elif change == "disable":
                principal = await session.get_one(PrincipalRow, service.tenant.principal_id)
                principal.status = "disabled"
            elif change == "archive":
                workspace = await session.get_one(WorkspaceRow, service.tenant.workspace_id)
                workspace.archived_at = await now(session)
            else:
                http.headers["authorization"] = "Bearer " + token[:-12] + "AAAAAAAAAAAA"
        assert (await rpc(http, "tools/list")).status_code == 401
    if change != "tamper":
        async with transaction(service.runtime.storage) as session:
            with pytest.raises(ServiceError):
                await reauthenticate(session, service.runtime.access, credential)


async def test_recovered_attempt_issues_new_credentials_and_rejects_old_worker(service, scripted_model, runs_kit):
    from a13n_service.runs.attempts import LeaseLost

    await runs_kit.pause_sweeps(service)
    lease, _, _ = await lease_for(service, scripted_model, runs_kit)
    token = await issue(service.runtime, lease, frozenset({"agents"}))
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(AttemptRow).where(AttemptRow.id == lease.attempt_id).values(lease_expires_at=AttemptRow.created_at)
        )
    await LeaseExpirer(service.runtime, batch=10)()
    async with transaction(service.runtime.storage) as session:
        run = await session.get_one(RunRow, lease.run_id)
        assert run.status == "accepted"
        run.available_at = await now(session)
    (replacement,) = await claim(service.runtime, worker_id="new-worker", worker_build="test", limit=1)
    assert replacement.attempt_id != lease.attempt_id
    async with client(service, {"secret": token}) as http:
        assert (await rpc(http, "tools/list")).status_code == 401
    with pytest.raises(LeaseLost):
        await issue(service.runtime, lease, frozenset({"agents"}))
    fresh = await issue(service.runtime, replacement, frozenset({"agents"}))
    async with client(service, {"secret": fresh}) as http:
        assert (await rpc(http, "tools/list")).status_code == 200


async def test_accepted_run_outlives_login_but_each_token_expires(service, scripted_model, runs_kit, monkeypatch):
    from datetime import timedelta

    from a13n_service.runs import platform_tokens

    lease, _, _ = await lease_for(service, scripted_model, runs_kit)
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(TokenRow)
            .where(TokenRow.principal_id == service.tenant.principal_id)
            .values(revoked_at=await now(session))
        )
    token = await issue(service.runtime, lease, frozenset({"agents"}))
    async with client(service, {"secret": token}) as http:
        assert (await rpc(http, "tools/list")).status_code == 200
    monkeypatch.setattr(platform_tokens, "_LIFETIME", timedelta(seconds=-1))
    expired = await issue(service.runtime, lease, frozenset({"agents"}))
    async with client(service, {"secret": expired}) as http:
        assert (await rpc(http, "tools/list")).status_code == 401


async def test_run_query_filters_pages_and_attempt_ownership(service, scripted_model, runs_kit):
    lease, agent, _ = await lease_for(service, scripted_model, runs_kit)
    other = await runs_kit.start_thread(service, agent, "another run")
    token = await issue(service.runtime, lease, frozenset({"execution"}))
    async with client(service, {"secret": token}) as http:
        first = await call(http, service, "GET", "/runs", agent_id=agent["id"], limit=1)
        page = first["body"]
        assert [item["id"] for item in page["items"]] == [other["run"]["id"]]
        cursor = page["next_cursor"]
        assert cursor
        second = await call(http, service, "GET", "/runs", agent_id=agent["id"], limit=1, cursor=cursor)
        assert [item["id"] for item in second["body"]["items"]] == [lease.run_id]
        assert second["body"]["next_cursor"] is None
        changed = await call(http, service, "GET", "/runs", status="running", limit=1, cursor=cursor)
        assert changed["status"] == 400
        timestamp = other["run"]["created_at"]
        after = await call(http, service, "GET", "/runs", created_after=timestamp)
        before = await call(http, service, "GET", "/runs", created_before=timestamp)
        assert [item["id"] for item in after["body"]["items"]] == [other["run"]["id"]]
        assert [item["id"] for item in before["body"]["items"]] == [lease.run_id]
        wrong_parent = await call(
            http,
            service,
            "GET",
            "/runs/{run_id}/attempts/{attempt_id}",
            run_id=other["run"]["id"],
            attempt_id=lease.attempt_id,
        )
        assert wrong_parent["status"] == 404


async def test_composer_discovers_and_calls_message_text_search(service, scripted_model, runs_kit):
    lease, agent, _ = await lease_for(service, scripted_model, runs_kit)
    matching = (await runs_kit.start_thread(service, agent, "南京部署故障"))["run"]
    token = await issue(service.runtime, lease, configuration("model").platform_tools.groups)
    async with client(service, {"secret": token}) as http:
        discovery = await rpc(http, "tools/list")
        assert discovery.status_code == 200, discovery.text
        tools = {tool["name"]: tool for tool in discovery.json()["result"]["tools"]}
        paths = service.app.openapi()["paths"]
        for resource, field in (("sessions", "session_id"), ("threads", "thread_id"), ("runs", "id")):
            operation = paths[f"/api/v1/{resource}"]["get"]
            tool = tools[tool_name(operation["operationId"])]
            assert "text_query" in tool["inputSchema"]["properties"]
            assert tool["_meta"]["x-a13n-platform"] == {"group": "execution", "effect": "read"}
            projection = {"view": "compact", "agent_id": agent["id"]} if resource == "runs" else {}
            result = await call(http, service, "GET", f"/{resource}", text_query="南京 故障", **projection)
            assert result["status"] == 200, result
            assert [row["id"] for row in result["body"]["items"]] == [matching[field]]
            missing = await call(http, service, "GET", f"/{resource}", text_query="南京 missing", **projection)
            assert missing["status"] == 200 and missing["body"]["items"] == []


async def test_legacy_revision_tool_requires_original_version(service, scripted_model, runs_kit):
    from a13n_service.runs.attempts import authorize_execution
    from a13n_service.runs.configuration import ConfigurationCapability
    from a13n_service.tenancy.authorize import ExecutionAuthority
    from pydantic_ai.exceptions import ToolFailed

    lease, agent, _ = await lease_for(service, scripted_model, runs_kit)
    async with transaction(service.runtime.storage) as session:
        run = await session.get_one(RunRow, lease.run_id)
        principal = await authorize_execution(session, service.runtime.access, run)
        authority = ExecutionAuthority.model_validate(run.authority)
    capability = ConfigurationCapability(service.runtime, lease, principal, authority, frozenset({"create_revision"}))
    revision = await service.client.get(f"/api/v1/agents/{agent['id']}/revisions/{agent['default_revision_id']}")
    config = revision.json()["config"]
    config["instructions"] = "Proposed change"
    with pytest.raises(ToolFailed, match="original version"):
        await capability.create_agent_revision(agent["id"], config)
    changed = await service.client.patch(
        f"/api/v1/agents/{agent['id']}",
        json={"description": "Concurrent edit"},
        headers={"If-Match": f'"{agent["id"]}:{agent["version"]}"'},
    )
    assert changed.status_code == 200, changed.text
    with pytest.raises(ToolFailed):
        await capability.create_agent_revision(agent["id"], config, expected_version=agent["version"])
    unchanged = (await service.client.get(f"/api/v1/agents/{agent['id']}")).json()
    assert unchanged["default_revision_id"] == agent["default_revision_id"]
    saved = await capability.create_agent_revision(agent["id"], config, expected_version=unchanged["version"])
    assert saved["revision_id"] != agent["default_revision_id"]


async def test_new_grants_do_not_widen_accepted_run(service, scripted_model, runs_kit):
    agent = await runs_kit.create_agent(service, scripted_model)
    async with transaction(service.runtime.storage) as session:
        await set_role(session, service, "runner")
    await runs_kit.start_thread(service, agent, "read only authority")
    (lease,) = await claim(service.runtime, worker_id="platform-test", worker_build="test", limit=1)
    token = await issue(service.runtime, lease, frozenset({"agents"}))
    credential = await authenticate_token(service, token)
    assert credential.kind == "run"
    assert credential.platform.groups == frozenset({"agents"})
    assert credential.platform.run_id == lease.run_id
    assert credential.platform.attempt_id == lease.attempt_id
    async with transaction(service.runtime.storage) as session:
        await set_role(session, service, "admin")
    async with transaction(service.runtime.storage) as session:
        refreshed = await reauthenticate(session, service.runtime.access, credential)
    workspace = WorkspaceScope(service.tenant.organization_id, service.tenant.workspace_id)
    assert refreshed.confinement == workspace
    assert allowed_verbs(refreshed, workspace) == frozenset({"read", "run"})
    async with client(service, {"secret": token}) as http:
        result = await call(
            http,
            service,
            "PATCH",
            "/agents/{agent_id}",
            agent_id=agent["id"],
            **{"If-Match": f'"{agent["id"]}:{agent["version"]}"'},
            request_body={"description": "not authorized"},
        )
        assert result["status"] == 403


async def test_native_platform_read_and_write_review_survive_resume(service, scripted_model, runs_kit, listen):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(
        service,
        scripted_model,
        platform_tools={"groups": ["agents"]},
        builtin_skills=["platform-operations"],
        toolsets={"files": {"enabled": True}, "shell": {"enabled": False}},
    )
    schema = service.app.openapi()["paths"]
    read_name = tool_name(schema["/api/v1/agents"]["get"]["operationId"])
    write_name = tool_name(schema["/api/v1/agents/{agent_id}"]["patch"]["operationId"])
    scripted_model.call("view", {"file_path": "/platform-skills/platform-operations/SKILL.md"}, call_id="guide")
    scripted_model.call(read_name, {}, call_id="read")
    scripted_model.call(
        write_name,
        {
            "agent_id": agent["id"],
            "If-Match": f'"{agent["id"]}:{agent["version"]}"',
            "request_body": {"description": "Reviewed change"},
        },
        call_id="write",
    )
    async with listen(service.app) as url:
        settings = service.runtime.settings
        runtime = replace(
            service.runtime,
            settings=settings.model_copy(update={"server": settings.server.model_copy(update={"control_url": url})}),
        )
        submitted = await runs_kit.start_thread(service, agent, "inspect and update")
        await (await runs_kit.attempt(service, runtime=runtime))
        run = await runs_kit.get_run(service, submitted["run"]["id"])
        assert run["status"] == "waiting", run
        unchanged = (await service.client.get(f"/api/v1/agents/{agent['id']}")).json()
        assert unchanged["description"] != "Reviewed change"
        scripted_model.say("Saved")
        resumed = await service.client.post(
            f"/api/v1/runs/{run['id']}/resume",
            json={"approvals": {"write": {"action": "approve"}}, "calls": {}},
            headers=runs_kit.fresh_key(),
        )
        assert resumed.status_code == 201, resumed.text
        await (await runs_kit.attempt(service, runtime=runtime))
        assert (await runs_kit.get_run(service, resumed.json()["id"]))["status"] == "completed"
        changed = (await service.client.get(f"/api/v1/agents/{agent['id']}")).json()
        assert changed["description"] == "Reviewed change"


async def test_provider_probe_waits_for_review_and_returns_saved_credential_status(
    service, scripted_model, runs_kit, listen
):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model, platform_tools={"groups": ["providers"]})
    with models_endpoint() as (base_url, requests, received):
        created = await service.client.post(
            "/api/v1/model-providers",
            json={
                "type": "openrouter",
                "name": "Probe target",
                "config": {"base_url": base_url},
                "credential": {"api_key": "good"},
            },
        )
        assert created.status_code == 201, created.text
        provider = created.json()
        path = "/api/v1/model-providers/{provider_id}"
        schema = service.app.openapi()["paths"]
        read_name = tool_name(schema[path]["get"]["operationId"])
        test_name = tool_name(schema[path + "/test"]["post"]["operationId"])
        scripted_model.call(read_name, {"provider_id": provider["id"]}, call_id="read")
        scripted_model.call(test_name, {"provider_id": provider["id"]}, call_id="probe")
        async with listen(service.app) as url:
            settings = service.runtime.settings
            runtime = replace(
                service.runtime,
                settings=settings.model_copy(
                    update={"server": settings.server.model_copy(update={"control_url": url})}
                ),
            )
            submitted = await runs_kit.start_thread(service, agent, "Test the saved provider")
            await (await runs_kit.attempt(service, runtime=runtime))
            run = await runs_kit.get_run(service, submitted["run"]["id"])
            assert run["status"] == "waiting", run
            assert requests == []
            scripted_model.say("Connection verified")
            resumed = await service.client.post(
                f"/api/v1/runs/{run['id']}/resume",
                json={"approvals": {"probe": {"action": "approve"}}, "calls": {}},
                headers=runs_kit.fresh_key(),
            )
            assert resumed.status_code == 201, resumed.text
            await (await runs_kit.attempt(service, runtime=runtime))
            assert (await runs_kit.get_run(service, resumed.json()["id"]))["status"] == "completed"
        assert requests == ["/v1/key"]
        assert received[0]["authorization"] == "Bearer good"
        current = await service.client.get(f"/api/v1/model-providers/{provider['id']}")
        assert current.json() == provider

    model_requests = [await scripted_model.request() for _ in range(3)]
    results = {
        message["tool_call_id"]: json.loads(message["content"])
        for message in model_requests[-1]["messages"]
        if message["role"] == "tool"
    }
    assert results["read"]["body"]["credential_configured"] is True
    assert "credential" not in results["read"]["body"]
    assert results["probe"]["body"] == {
        "provider_id": provider["id"],
        "provider_version": provider["version"],
        "status": "succeeded",
        "message": None,
    }


async def test_inline_child_reads_builtin_skill_without_parent_skill_or_user_environment(
    service, scripted_model, runs_kit
):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.delegating(
        service,
        scripted_model,
        "inline",
        worker={
            "builtin_skills": ["platform-operations"],
            "toolsets": {"files": {"enabled": True}, "shell": {"enabled": False}},
        },
    )
    scripted_model.call(
        "delegate", {"subagent": "helper", "prompt": "read the guide"}, call_id="delegate", to="coordinator"
    )
    scripted_model.call(
        "view", {"file_path": "/platform-skills/platform-operations/SKILL.md"}, call_id="guide", to="worker"
    )
    scripted_model.say("Guide read", to="worker")
    scripted_model.say("Done", to="coordinator")
    submitted = await runs_kit.start_thread(service, agent, "ask the helper")
    await (await runs_kit.attempt(service))
    run = await runs_kit.get_run(service, submitted["run"]["id"])
    assert run["status"] == "completed" and run["output"] == "Done", run
    requests = [await scripted_model.request() for _ in range(4)]
    assert "# Platform operations" in json.dumps(requests)
