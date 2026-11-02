"""Platform admission and deferred writes preserve the reviewed execution boundary."""

import json
import re
from dataclasses import replace

import pytest
from a13n_harness.tools.identity import TOOL_IDENTITY_KEY, ToolIdentity, source_tool_id
from a13n_service.infra.platform import PLATFORM_GROUPS
from a13n_service.runs.platform_tokens import issue
from a13n_service.runs.platform_tools import PlatformToolset

from .conftest import add_agent
from .mcp_support import key
from .mcp_support import rpc as external_rpc
from .platform_support import call, client, rpc
from .test_platform_tools import lease_for

pytestmark = pytest.mark.anyio


async def test_run_credentials_admit_only_internal_platform_transport(service, scripted_model, runs_kit):
    lease, _, _ = await lease_for(service, scripted_model, runs_kit)
    token = await issue(service.runtime, lease, frozenset(PLATFORM_GROUPS))
    async with client(service, {"secret": token}) as http:
        assert (await rpc(http, "tools/list")).status_code == 200
        assert (await external_rpc(http, "tools/list")).status_code == 401
        # Every documented direct operation must reject before routing, validation or effects.
        for template, operations in service.app.openapi()["paths"].items():
            path = re.sub(r"\{[^}]+\}", "unavailable", template)
            for method in operations.keys() & {"get", "post", "put", "patch", "delete", "head", "options"}:
                response = await http.request(method, path)
                assert response.status_code == 403, (method, path, response.text)
        for path in ("/healthz", "/api/v1/openapi.json"):
            assert (await http.get(path)).status_code == 403
    async with client(service, await key(service)) as http:
        assert (await external_rpc(http, "tools/list")).status_code == 200
        assert (await rpc(http, "tools/list")).status_code == 401
        assert (await rpc(http, "tools/call", name="list_agents", arguments={})).status_code == 401


async def test_old_names_and_payloads_fail_before_business_effects(service, scripted_model, runs_kit):
    lease, agent, _ = await lease_for(service, scripted_model, runs_kit)
    token = await issue(service.runtime, lease, frozenset(PLATFORM_GROUPS))
    async with client(service, {"secret": token}) as http:
        before = await call(http, "list_agents")
        for name in (
            "create_agent_revision",
            "update_agent",
            "index_toolsets",
            "get_run_agent",
            "read_span_content",
            "get_run_content",
            "read_run_item_content",
        ):
            rejected = await rpc(http, "tools/call", name=name, arguments={})
            assert rejected.json()["result"]["isError"], name
        for name, arguments in (
            ("create_agent", {"request_body": {"name": "Old payload"}}),
            ("update_agent_metadata", {"agent_id": agent["id"], "description": "Unversioned write"}),
        ):
            rejected = await rpc(http, "tools/call", name=name, arguments=arguments)
            assert rejected.json()["result"]["isError"], rejected.text
        assert (await call(http, "list_agents"))["items"] == before["items"]
        unchanged = await call(http, "get_agent", agent_id=agent["id"])
        assert unchanged["version"] == agent["version"]
        assert unchanged["description"] != "Unversioned write"


@pytest.mark.parametrize("boundary", ["stale_version", "old_source"])
async def test_reviewed_write_never_rebinds_target_delta_version_or_source(
    service, scripted_model, runs_kit, monkeypatch, boundary
):
    await runs_kit.pause_sweeps(service)
    target = await runs_kit.create_agent(service, scripted_model)
    editor = await add_agent(service, "Editor", "scripted", toolsets={"agents": {"enabled": True}})
    arguments = {
        "agent_id": target["id"],
        "expected_version": f"{target['id']}:{target['version']}",
        "description": "Reviewed delta",
    }
    original = PlatformToolset.get_tools

    async def old_identity(self, ctx):
        tools = await original(self, ctx)
        tool = tools["update_agent_metadata"]
        tools["update_agent_metadata"] = replace(
            tool,
            tool_def=replace(
                tool.tool_def,
                metadata={
                    **tool.tool_def.metadata,
                    TOOL_IDENTITY_KEY: ToolIdentity(
                        source_tool_id("platform", "update_agent_metadata", kind="mcp"), "ask"
                    ),
                },
            ),
        )
        return tools

    if boundary == "old_source":
        monkeypatch.setattr(PlatformToolset, "get_tools", old_identity)
    scripted_model.call("update_agent_metadata", arguments, call_id="write")
    async with runs_kit.platform_runtime(service) as runtime:
        submitted = await runs_kit.start_thread(service, editor, "apply the reviewed change")
        await (await runs_kit.attempt(service, runtime=runtime))
        waiting = await runs_kit.get_run(service, submitted["run"]["id"])
        assert waiting["status"] == "waiting", waiting
        approval = waiting["pending"]["approvals"][0]
        assert approval["tool_call_id"] == "write"
        assert approval["arguments"] == arguments
        if boundary == "stale_version":
            changed = await service.client.patch(
                f"/api/v1/agents/{target['id']}",
                json={"description": "Concurrent change"},
                headers={"if-match": f'"{target["id"]}:{target["version"]}"'},
            )
            assert changed.status_code == 200, changed.text
            expected = changed.json()
        else:
            expected = target
            monkeypatch.setattr(PlatformToolset, "get_tools", original)
        scripted_model.say("Finished checking the outcome")
        resumed = await service.client.post(
            f"/api/v1/runs/{waiting['id']}/resume",
            json={"approvals": {"write": {"action": "approve"}}, "calls": {}},
            headers=runs_kit.fresh_key(),
        )
        assert resumed.status_code == 201, resumed.text
        await (await runs_kit.attempt(service, runtime=runtime))
        actual = (await service.client.get(f"/api/v1/agents/{target['id']}")).json()
        assert actual["version"] == expected["version"]
        assert actual["description"] == expected["description"]
        requests = []
        while not scripted_model.requests.empty():
            requests.append(scripted_model.requests.get_nowait())
        if boundary == "stale_version":
            assert "precondition_failed" in json.dumps(requests)
        else:
            resumed_run = await runs_kit.get_run(service, resumed.json()["id"])
            assert resumed_run["status"] == "failed", resumed_run
            assert "pending target identity changed" in json.dumps(resumed_run), resumed_run


async def test_unknown_write_response_is_not_replayed(service, scripted_model, runs_kit, monkeypatch):
    import httpx2

    await runs_kit.pause_sweeps(service)
    target = await runs_kit.create_agent(service, scripted_model)
    editor = await add_agent(service, "Editor", "scripted", toolsets={"agents": {"enabled": True}})
    scripted_model.call(
        "update_agent_metadata",
        {
            "agent_id": target["id"],
            "expected_version": f"{target['id']}:{target['version']}",
            "description": "Committed before disconnect",
        },
        call_id="write",
    )
    sends = 0
    original = httpx2.AsyncClient.send

    async def lose_response(self, request, *args, **kwargs):
        nonlocal sends
        response = await original(self, request, *args, **kwargs)
        if request.url.path == "/api/v1/platform-tools/" and request.method == "POST":
            body = json.loads(request.content)
            if body.get("method") == "tools/call" and body.get("params", {}).get("name") == "update_agent_metadata":
                sends += 1
                # Drain the real successful response: the business mutation is committed.
                await response.aread()
                await response.aclose()
                raise httpx2.ReadError("Response lost after committed write", request=request)
        return response

    async with runs_kit.platform_runtime(service) as runtime:
        submitted = await runs_kit.start_thread(service, editor, "make the change")
        await (await runs_kit.attempt(service, runtime=runtime))
        waiting = await runs_kit.get_run(service, submitted["run"]["id"])
        assert waiting["status"] == "waiting", waiting
        monkeypatch.setattr(httpx2.AsyncClient, "send", lose_response)
        scripted_model.say("The response was lost; inspect before retrying")
        resumed = await service.client.post(
            f"/api/v1/runs/{waiting['id']}/resume",
            json={"approvals": {"write": {"action": "approve"}}, "calls": {}},
            headers=runs_kit.fresh_key(),
        )
        assert resumed.status_code == 201, resumed.text
        await (await runs_kit.attempt(service, runtime=runtime))
        assert sends == 1
        actual = (await service.client.get(f"/api/v1/agents/{target['id']}")).json()
        assert actual["description"] == "Committed before disconnect"
        assert actual["version"] == target["version"] + 1
