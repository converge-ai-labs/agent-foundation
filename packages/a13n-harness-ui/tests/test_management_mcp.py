"""Exercise inbound management with a real MCP client and the same App as WebUI HTTP."""

from pathlib import Path

import httpx
import pytest
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.share_models import ShareCreate
from a13n_harness_ui.surfaces import RootControlResult, RootOperationStatus
from anyio import Event, fail_after
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from .test_app import _CompletedReconstructor, _DeferredReconstructor, _SlowReconstructor, _write_configuration
from .test_interactive_protocol import listener_with_app
from .test_webui_access import webui

pytestmark = pytest.mark.anyio

_PATH = "/api/automation/mcp"
_AUTH = {"Authorization": "Bearer test-only-key"}


async def test_mcp_manages_projects_and_threads_on_the_webui_listener(tmp_path: Path, monkeypatch) -> None:
    configuration = _write_configuration(tmp_path)
    async with listener_with_app(tmp_path, sharing=False, configuration_path=configuration) as (url, _, app):
        app._root_runs._executor._agents = _CompletedReconstructor()
        async with (
            Client(StreamableHttpTransport(url + _PATH, headers=_AUTH)) as mcp,
            httpx.AsyncClient(base_url=url, headers=_AUTH) as http,
        ):
            tools = {tool.name: tool.input_schema for tool in await mcp.list_tools()}
            assert set(tools) == {
                "list_projects",
                "get_project",
                "create_project",
                "list_threads",
                "get_thread",
                "create_thread",
                "send_thread_message",
                "post_to_inbox",
            }
            assert tools["get_thread"]["required"] == ["thread_id"]
            assert tools["create_thread"]["required"] == ["prompt"]
            assert set(tools["send_thread_message"]["required"]) == {"thread_id", "message"}

            async def call(tool_name, **arguments):
                result = await mcp.call_tool(tool_name, arguments)
                assert result.structured_content is not None
                return result.structured_content

            project = await call("create_project", roots=[str(tmp_path)], name="MCP project")
            assert project["status"] == "created"
            project_id = project["project"]["id"]
            assert (await call("get_project", project_id=project_id))["project"]["roots"] == [str(tmp_path)]
            assert project_id in str(await call("list_projects", query="MCP project"))
            assert project_id in str((await http.get("/api/projects")).json())

            created = await call("create_thread", prompt="Initial MCP prompt", project_id=project_id, title="MCP work")
            assert created["ok"] is True
            thread_id = created["thread_id"]
            assert created["receipt"]["thread_id"] == thread_id
            guest = await app.sharing.create(ShareCreate(scope="thread", target_id=thread_id))
            denied = await http.post(
                _PATH,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Authorization": f"Bearer {guest.key}"},
            )
            assert denied.status_code == 401
            receipt_id = created["receipt"]["receipt_id"]
            assert (
                await app.wait_root_operation(receipt_id, timeout_seconds=5)
            ).status is RootOperationStatus.completed
            thread = await call("get_thread", thread_id=thread_id, history_limit=10)
            assert "root complete" in str(thread["transcript"])
            assert "Initial MCP prompt" in str(thread["transcript"])
            assert (await http.get(f"/api/threads/{thread_id}")).json() == thread["thread"]
            assert thread_id in str(await call("list_threads", project_id=project_id))

            started = Event()
            app._root_runs._executor._agents = _SlowReconstructor(started)
            followup = await call("send_thread_message", thread_id=thread_id, message="Run while we test steering")
            assert followup["mode"] == "run"
            receipt_id = followup["receipt"]["receipt_id"]
            with fail_after(5):
                await started.wait()
            steer = await call("send_thread_message", thread_id=thread_id, message="Steer this Run")
            assert steer["ok"] and steer["mode"] == "steer" and steer["receipt_id"] == receipt_id

            async def reject_steer(*, receipt_id, message):
                return RootControlResult(receipt_id=receipt_id, accepted=False)

            monkeypatch.setattr(app._root_runs, "steer", reject_steer)
            rejected = await call("send_thread_message", thread_id=thread_id, message="Do not start another Run")
            assert rejected["ok"] is False and rejected["mode"] == "steer" and rejected["receipt_id"] == receipt_id
            await app.cancel_root_operation(receipt_id)
            await app.wait_root_operation(receipt_id, timeout_seconds=5)

            async def reject_admission(**kwargs):
                raise HarnessUiError("admission rejected", code="run_rejected")

            monkeypatch.setattr(app._root_runs, "submit_prompt", reject_admission)
            partial = await call("create_thread", prompt="First prompt fails")
            assert partial["ok"] is False and partial["error"]["code"] == "run_rejected"
            created_thread = await call("get_thread", thread_id=partial["thread_id"])
            assert created_thread["ok"] and created_thread["thread"]["thread"]["configuration"]["project_id"] is None


async def test_mcp_posts_to_the_same_inbox_without_starting_a_run(tmp_path: Path) -> None:
    configuration = _write_configuration(tmp_path)
    async with listener_with_app(tmp_path, sharing=False, configuration_path=configuration) as (url, _, app):
        thread = await app.create_thread()
        coordinator = await app.create_thread(coordinator=True)
        worker = await app.create_thread(coordinator_thread_id=coordinator.thread_id)
        live = await app._threads.live_thread(configuration_root=tmp_path)
        async with (
            Client(StreamableHttpTransport(url + _PATH, headers=_AUTH)) as mcp,
            httpx.AsyncClient(base_url=url, headers=_AUTH) as http,
        ):
            tool = next(tool for tool in await mcp.list_tools() if tool.name == "post_to_inbox")
            schema = tool.input_schema
            assert set(schema["required"]) == {"thread_id", "kind", "title", "message"}
            assert schema["properties"]["kind"]["enum"] == ["result", "needs_human", "milestone"]
            for field, maximum in (("title", 120), ("message", 4096)):
                assert schema["properties"][field]["minLength"] == 1
                assert schema["properties"][field]["maxLength"] == maximum

            payload = {
                "thread_id": thread.thread_id,
                "kind": "needs_human",
                "title": "Approve the plan?",
                "message": "Reply **yes** to proceed.",
            }

            async def post(**overrides):
                result = await mcp.call_tool("post_to_inbox", payload | overrides)
                assert result.structured_content is not None
                return result.structured_content

            assert (await post(thread_id="thread_missing"))["error"]["code"] == "thread_missing"
            for ineligible_id in (worker.thread_id, live.thread_id):
                assert (await post(thread_id=ineligible_id))["error"]["code"] == "inbox_thread_ineligible"
            posted = await post()
            assert posted["ok"] is True
            response = await http.get("/api/inbox")
            response.raise_for_status()
            page = response.json()
            assert page["unread_count"] == 1
            (item,) = page["items"]
            assert item["item_id"] == posted["item_id"]
            assert {field: item[field] for field in payload} == payload
            location = await http.get(f"/api/inbox/{item['item_id']}/location")
            location.raise_for_status()
            assert location.json() == {"thread_id": thread.thread_id, "continuation_id": None, "turn_id": None}
            assert await app.active_root_operation(thread.thread_id) is None
            assert (await app.get_thread(thread.thread_id)).continuation_id is None

            with configuration.open("a") as config:
                config.write("inbox: {enabled: false}\n")
            await app.reload_configuration()
            assert (await post())["error"]["code"] == "inbox_disabled"
            assert (await http.get("/api/inbox")).json() == page


async def test_mcp_creation_model_applies_only_to_the_first_run(tmp_path: Path) -> None:
    configuration = _write_configuration(tmp_path, allow_agent_creation=True)
    (tmp_path / "models/secondary.yaml").write_text(
        "schema_version: '1'\nkind: model\nid: model-secondary\nname: Secondary\nroute: openai:gpt-5\n"
        "authentication: {kind: api_key, env: OPENAI_API_KEY}\n"
    )
    async with listener_with_app(tmp_path, sharing=False, configuration_path=configuration) as (url, _, app):
        app._root_runs._executor._agents = _CompletedReconstructor()
        async with Client(StreamableHttpTransport(url + _PATH, headers=_AUTH)) as mcp:

            async def completed_model(tool_result):
                result = tool_result.structured_content
                assert result is not None and result["ok"]
                receipt_id = result["receipt"]["receipt_id"]
                operation = await app.wait_root_operation(receipt_id, timeout_seconds=5)
                assert operation.status is RootOperationStatus.completed
                return (await app.inspect_operation_configuration(receipt_id)).agent.model_id

            # The owner can select a configured Model outside the in-Run creation allowlist.
            created = await mcp.call_tool("create_thread", {"prompt": "First", "model_id": "model-secondary"})
            assert await completed_model(created) == "model-secondary"
            thread_id = created.structured_content["thread_id"]
            assert (await app.get_thread(thread_id)).thread.configuration.default_model_id is None

            followup = await mcp.call_tool("send_thread_message", {"thread_id": thread_id, "message": "Next"})
            assert await completed_model(followup) == "model-primary"


async def test_mcp_send_rejects_decisions_even_after_a_stale_detail_read(tmp_path: Path, monkeypatch) -> None:
    configuration = _write_configuration(tmp_path)
    async with listener_with_app(tmp_path, sharing=False, configuration_path=configuration) as (url, _, app):
        app._root_runs._executor._agents = _DeferredReconstructor()
        thread = await app.create_thread()
        stale = await app.get_thread(thread.thread_id)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Wait for a decision")
        settled = await app.wait_root_operation(receipt.receipt_id, timeout_seconds=5)
        assert settled.status is RootOperationStatus.suspended
        before = await app.get_thread(thread.thread_id)
        assert before.deferred_requests

        async def stale_detail(thread_id):
            assert thread_id == thread.thread_id
            return stale

        with monkeypatch.context() as patch:
            patch.setattr(app._projections, "detail", stale_detail)
            async with Client(StreamableHttpTransport(url + _PATH, headers=_AUTH)) as mcp:
                result = await mcp.call_tool("send_thread_message", {"thread_id": thread.thread_id, "message": "Hello"})
                assert result.structured_content is not None
                assert result.structured_content["error"]["code"] == "thread_deferred_pending"
        after = await app.get_thread(thread.thread_id)
        assert await app.active_root_operation(thread.thread_id) is None
        assert after.continuation_id == before.continuation_id
        assert after.deferred_requests == before.deferred_requests


async def test_mcp_reuses_the_existing_webui_access_boundary(tmp_path: Path) -> None:
    async with (
        webui(tmp_path, []) as (server, _),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://localhost") as http,
    ):
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        for auth in ({}, {"Authorization": "Bearer not-the-instance-key"}):
            response = await http.post(_PATH, json=rpc, headers=auth)
            assert response.status_code == 401
        assert (await http.post(_PATH, json=rpc, headers={**_AUTH, "Host": "example.invalid"})).status_code == 400
        rejected = await http.post(_PATH, json=rpc, headers={**_AUTH, "Origin": "https://example.invalid"})
        assert rejected.status_code == 403
        # The existing outbound integration API still resolves normally.
        existing = await http.get("/api/mcp/missing/tools", headers=_AUTH)
        assert existing.json()["error"]["code"] == "configuration_resource_missing"

    async with (
        webui(tmp_path, [], api_key=None) as (server, _),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://localhost") as http,
    ):
        accepted = await http.post(_PATH, json=rpc, headers={"Accept": "application/json, text/event-stream"})
        assert accepted.status_code == 200
        assert "tools" in accepted.json()["result"]
