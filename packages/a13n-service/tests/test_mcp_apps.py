"""MCP App originals cross worker persistence and independent, authorized control requests."""

from dataclasses import replace
from types import SimpleNamespace

import anyio
import pytest
from a13n_service.runs.contents import Contents
from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from mcp.types import TextContent

from .test_connections import create, patch, serve

pytestmark = pytest.mark.anyio
URI = "ui://counter/app.html"
MIME = "text/html;profile=mcp-app"


@pytest.fixture
async def app_run(service, scripted_model, runs_kit, request):
    server = FastMCP("App counter")
    calls = SimpleNamespace(show=0, increment=0, resource=0, html="<p>First version</p>", slow=False)

    @server.tool(meta={"ui": {"resourceUri": URI}})
    def show_counter(label: str) -> ToolResult:
        calls.show += 1
        return ToolResult(
            content=[TextContent(type="text", text=label)],
            structured_content={"count": 0},
            meta={"private_app_value": "original-private-value"},
            is_error=getattr(request, "param", False),
        )

    @server.tool(meta={"ui": {"visibility": ["app"]}})
    async def increment() -> ToolResult:
        calls.increment += 1
        if calls.slow:
            await anyio.sleep(5)
        return ToolResult(content="incremented", structured_content={"count": calls.increment}, meta={"action": True})

    @server.tool(meta={"ui": {"visibility": ["model"]}})
    def hidden() -> str:
        raise AssertionError("A model-only tool must never be called by the App")

    @server.tool(meta={"ui": {"visibility": ["app"]}})
    def reset() -> str:
        raise AssertionError("An approval-only tool must never be called by the App")

    @server.resource(URI, mime_type=MIME, meta={"ui": {"csp": {"connectDomains": ["https://app.test"]}}})
    def resource() -> str:
        calls.resource += 1
        return calls.html

    async with serve(server.http_app(path="/mcp")) as url:
        connection = await create(service, {"config": {"url": url + "/mcp"}})
        agent = await runs_kit.create_agent(
            service,
            scripted_model,
            connection_tools=[{"connection_id": connection["id"], "permissions": {"reset": "ask"}}],
            toolsets={"files": {"enabled": False}, "shell": {"enabled": False}},
        )
        scripted_model.call("show_counter", {"label": "Counter"}, call_id="call_show")
        scripted_model.say("Opened")
        submitted = await runs_kit.start_thread(service, agent, "Show the counter")
        await (await runs_kit.attempt(service))
        run_id = submitted["run"]["id"]
        run = await runs_kit.get_run(service, run_id)
        assert run["status"] == "completed", run
        history = await runs_kit.items(service, run_id)
        refs = [app for item in history["items"] for app in item["content"].get("mcp_apps", [])]
        assert len(refs) == 1, history
        ref = refs[0]
        yield SimpleNamespace(
            path=f"{service.api}/runs/{run_id}/apps/{ref['app_id']}",
            run_id=run_id,
            reference=ref,
            calls=calls,
            connection=connection,
            agent=agent,
            history=history,
        )


async def test_worker_saves_original_and_control_reopens_fresh_html_without_replaying(
    service,
    scripted_model,
    runs_kit,
    app_run,
):
    calls, path = app_run.calls, app_run.path
    assert calls.show == 1 and calls.resource == 0
    first_request = await scripted_model.request()
    second_request = await scripted_model.request()
    assert "increment" not in str(first_request)
    assert "original-private-value" not in str(second_request)
    assert "original-private-value" not in str(app_run.history)
    assert (
        await service.client.get(f"{service.api}/runs/{app_run.run_id}/contents/{app_run.reference['app_id']}")
    ).status_code == 404
    first = await service.client.post(path + "/open")
    assert first.status_code == 200, first.text
    original = first.json()
    assert original["arguments"] == {"label": "Counter"}
    assert original["result"]["structuredContent"] == {"count": 0}
    assert original["result"]["_meta"]["private_app_value"] == "original-private-value"
    assert original["tool"]["_meta"]["ui"]["resourceUri"] == URI
    assert original["resource"]["mime_type"] == MIME
    assert original["resource"]["metadata"]["ui"]["csp"]["connectDomains"] == ["https://app.test"]
    assert "connection_url" not in original and "selection" not in original
    changed = await service.client.post(path + "/tools/call", json={"name": "increment"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["result"]["structuredContent"] == {"count": 1}
    assert changed.json()["result"]["_meta"]["action"] is True
    calls.html = "<p>New version</p>"
    reopened = await service.client.post(path + "/open")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["result"] == original["result"]
    assert reopened.json()["resource"]["html"] == calls.html
    resource = await service.client.post(path + "/resources/read", json={"uri": URI})
    assert resource.status_code == 200 and resource.json()["html"] == calls.html
    assert (calls.show, calls.increment, calls.resource) == (1, 1, 3)
    assert (await runs_kit.get_run(service, app_run.run_id))["usage_at_seal"]["requests"] == 2
    assert await runs_kit.items(service, app_run.run_id) == app_run.history


async def test_control_rejects_wrong_run_hidden_tools_permissions_and_disabled_connection(service, runs_kit, app_run):
    path = app_run.path
    other = await runs_kit.start_thread(service, app_run.agent, "another run")
    wrong = f"{service.api}/runs/{other['run']['id']}/apps/{app_run.reference['app_id']}/open"
    assert (await service.client.post(wrong)).status_code == 404
    for name in ("hidden", "reset", "missing"):
        response = await service.client.post(path + "/tools/call", json={"name": name})
        assert response.status_code == 403, response.text
    response = await service.client.post(path + "/resources/read", json={"uri": "ui://another/app.html"})
    assert response.status_code == 400, response.text
    connection = await patch(
        service, app_run.connection, {"config": {"url": app_run.connection["config"]["url"], "tools": ["show_counter"]}}
    )
    response = await service.client.post(path + "/tools/call", json={"name": "increment"})
    assert response.status_code == 403, response.text
    await patch(service, connection, {"enabled": False})
    assert (await service.client.post(path + "/open")).status_code == 422
    assert app_run.calls.increment == 0 and app_run.calls.resource == 0


async def test_unknown_outcome_is_not_retried_and_admission_must_opt_in(service, app_run):
    path = app_run.path
    runtime = service.runtime
    service.app.state.runtime = replace(runtime, admission=SimpleNamespace())
    rejected = await service.client.post(path + "/tools/call", json={"name": "increment"})
    assert rejected.status_code == 403, rejected.text
    assert app_run.calls.increment == 0
    admitted = []

    async def proceed_app(session, call):
        admitted.append(call)

    service.app.state.runtime = replace(runtime, admission=SimpleNamespace(proceed_app=proceed_app))
    response = await service.client.post(path + "/tools/call", json={"name": "increment"})
    assert response.status_code == 200, response.text
    assert admitted[0].run_id == app_run.run_id and admitted[0].app_id == app_run.reference["app_id"]
    assert not hasattr(admitted[0], "run_attempt_id")
    service.app.state.runtime = replace(
        runtime,
        settings=runtime.settings.model_copy(
            update={"providers": runtime.settings.providers.model_copy(update={"tool_call_seconds": 0.5})}
        ),
    )
    app_run.calls.slow = True
    response = await service.client.post(path + "/tools/call", json={"name": "increment"})
    assert response.status_code == 503, response.text
    assert response.json()["error"]["details"]["reason"] == "outcome_unknown"
    assert app_run.calls.increment == 2


@pytest.mark.parametrize("count", [1, 128])
async def test_app_reference_survives_tail_restore_and_page_dependencies(runtime, tenant, count):
    from a13n_service.runs import checkpoints
    from a13n_service.runs.attempts import Lease
    from a13n_service.runs.display import Page, Snapshot, Tail

    from .test_display_contents import item

    lease = Lease("run_apps", "rat_apps", "thr_apps", tenant.organization_id, tenant.workspace_id, 1, "w", "t")
    contents = Contents()
    for index in range(count):
        assert contents.app(f"app_{index}", b'{"original":true}')
    value = item(
        [
            {"app_id": f"app_{index}", "unavailable": False, "resource_uri": "ui://" + "x" * 400}
            for index in range(count)
        ],
        "mcp_apps",
    )
    first = await checkpoints.publish_display(runtime, lease, Snapshot([], Tail(items=[value])), contents=contents)
    assert sum(ref.purpose == "app" for ref in first.tail.refs) == count
    tail = await checkpoints.load_tail(runtime.objects, first.tail, hydrate=True)
    restored = Contents(tail.items, tail.refs)
    second = await checkpoints.publish_display(runtime, lease, Snapshot([], tail), contents=restored)
    assert second.tail.refs == first.tail.refs
    original_ref = next(ref for ref in second.tail.refs if ref.purpose == "app")
    assert await checkpoints.load(runtime.objects, original_ref) == b'{"original":true}'
    paged = await checkpoints.publish_display(
        runtime, lease, Snapshot([Page(items=tail.items)], Tail(first=2)), contents=restored
    )
    assert {ref.id for ref in paged.pages[0].refs} == {ref.id for ref in second.tail.refs}
    restored.retire([value.id])
    assert not restored.refs


async def test_app_storage_failure_marks_unavailable_without_retrying_publication():
    from .test_display_contents import item

    attempts = 0

    async def fail(data):
        nonlocal attempts
        attempts += 1
        raise OSError("object store unavailable")

    contents = Contents()
    contents.app("app_one", b'{"original":true}')
    value = item([{"app_id": "app_one", "unavailable": False}], "mcp_apps")
    for _ in range(2):
        saved = await contents.item(value, fail)
        assert saved.content["mcp_apps"] == [{"app_id": "app_one", "unavailable": True}]
        assert contents.dependencies([saved]) == ()
    assert attempts == 1 and not contents.pending_apps


@pytest.mark.parametrize("app_run", [True], indirect=True)
async def test_original_mcp_error_is_retained_without_being_replayed(service, app_run):
    response = await service.client.post(app_run.path + "/open")
    assert response.status_code == 200, response.text
    assert response.json()["result"]["isError"] is True
    assert response.json()["result"]["_meta"]["private_app_value"] == "original-private-value"
    assert app_run.calls.show == 1
