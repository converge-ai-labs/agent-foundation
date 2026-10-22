"""MCP App originals cross worker persistence and independent, authorized control requests."""

import json
from dataclasses import replace
from types import SimpleNamespace

import anyio
import pytest
from a13n_service.resources.connections.schemas import ConnectionSelection
from a13n_service.runs.contents import Contents
from a13n_service.runs.mcp_apps.schemas import AppBinding, AppReference
from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from mcp.types import TextContent

from .test_connections import admin, create, patch, serve

pytestmark = pytest.mark.anyio
URI = "ui://counter/app.html"
MIME = "text/html;profile=mcp-app"


@pytest.fixture
async def app_run(service, scripted_model, runs_kit, request, monkeypatch):
    mode = getattr(request, "param", False)
    if mode == "legacy":
        save_app = Contents.app

        def legacy_app(self, identifier, data, *, binding=None):
            # Format-4 writers included private authorization in the original, not its reference.
            assert binding is not None
            legacy = {**json.loads(data), **binding.model_dump(mode="json")}
            return save_app(self, identifier, json.dumps(legacy).encode())

        monkeypatch.setattr(Contents, "app", legacy_app)
    server = FastMCP("App counter")
    calls = SimpleNamespace(show=0, increment=0, resource=0, html="<p>First version</p>", slow=False, failed=False)

    @server.tool(meta={"ui": {"resourceUri": URI}})
    def show_counter(label: str) -> ToolResult:
        calls.show += 1
        return ToolResult(
            content=[TextContent(type="text", text=label)],
            structured_content={"count": 0},
            meta={"private_app_value": "original-private-value"},
            is_error=mode is True,
        )

    @server.tool(meta={"ui": {"visibility": ["app"]}})
    async def increment() -> ToolResult:
        calls.increment += 1
        if calls.slow:
            await anyio.sleep(5)
        return ToolResult(
            content="incremented",
            structured_content={"count": calls.increment},
            meta={"action": True},
            is_error=calls.failed,
        )

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
        runtime = service.runtime
        if mode == "paged":
            settings = runtime.settings
            runtime = replace(
                runtime,
                settings=settings.model_copy(update={"worker": settings.worker.model_copy(update={"page_items": 1})}),
            )
        await (await runs_kit.attempt(service, runtime=runtime))
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
            paged=mode == "paged",
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
    from a13n_service.runs.mcp_apps.service import original as load_original

    saved = await load_original(
        service.runtime, admin(service), service.tenant.workspace_id, app_run.run_id, app_run.reference["app_id"]
    )
    assert "resource" not in saved.model_dump() and "html" not in saved.model_dump()
    assert calls.html not in saved.model_dump_json()


async def test_app_button_preserves_raw_error_result_without_retry(service, app_run):
    app_run.calls.failed = True
    response = await service.client.post(app_run.path + "/tools/call", json={"name": "increment"})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"] == {"count": 1}
    assert result["_meta"]["action"] is True
    assert app_run.calls.increment == 1 and app_run.calls.show == 1


@pytest.mark.parametrize("app_run", ["tail", "paged"], indirect=True)
async def test_app_operations_read_only_committed_database_binding(service, app_run, monkeypatch):
    from a13n_service.infra.db import short_session
    from a13n_service.runs import checkpoints, runs
    from a13n_service.runs.tables import RunRow

    runtime = service.runtime
    ref = await runs.content_ref(
        runtime, admin(service), service.tenant.workspace_id, app_run.run_id, app_run.reference["app_id"], purpose="app"
    )
    assert ref.app is not None
    assert ref.app.reference.model_dump() == app_run.reference
    assert ref.app.selection.permissions == {"reset": "ask"}
    raw = json.loads(await checkpoints.load(runtime.objects, ref))
    assert "connection_url" not in raw and "selection" not in raw and "run_id" not in raw
    async with short_session(runtime.storage) as session:
        run = await session.get_one(RunRow, app_run.run_id)
        tail_has_app = any(value["id"] == ref.id for value in run.tail["refs"])
    assert tail_has_app is not app_run.paged

    reads = []
    get = runtime.objects.get

    async def counted_get(key):
        reads.append(key)
        return await get(key)

    monkeypatch.setattr(runtime.objects, "get", counted_get)
    opened = await service.client.post(app_run.path + "/open")
    assert opened.status_code == 200, opened.text
    assert reads == [ref.key]
    reads.clear()
    for name in ("hidden", "reset", "missing"):
        assert (await service.client.post(app_run.path + "/tools/call", json={"name": name})).status_code == 403
    called = await service.client.post(app_run.path + "/tools/call", json={"name": "increment"})
    assert called.status_code == 200, called.text
    resource = await service.client.post(app_run.path + "/resources/read", json={"uri": URI})
    assert resource.status_code == 200, resource.text
    assert resource.json()["html"] == app_run.calls.html
    assert reads == []


@pytest.mark.parametrize("app_run", ["legacy"], indirect=True)
async def test_legacy_app_reference_loads_authorization_from_original(service, app_run, monkeypatch):
    from a13n_service.runs import runs

    runtime = service.runtime
    ref = await runs.content_ref(
        runtime, admin(service), service.tenant.workspace_id, app_run.run_id, app_run.reference["app_id"], purpose="app"
    )
    assert ref.app is None
    reads = []
    get = runtime.objects.get

    async def counted_get(key):
        reads.append(key)
        return await get(key)

    monkeypatch.setattr(runtime.objects, "get", counted_get)
    opened = await service.client.post(app_run.path + "/open")
    assert opened.status_code == 200, opened.text
    assert opened.json()["result"]["_meta"]["private_app_value"] == "original-private-value"
    called = await service.client.post(app_run.path + "/tools/call", json={"name": "increment"})
    assert called.status_code == 200, called.text
    resource = await service.client.post(app_run.path + "/resources/read", json={"uri": URI})
    assert resource.status_code == 200, resource.text
    denied = await service.client.post(app_run.path + "/tools/call", json={"name": "reset"})
    assert denied.status_code == 403, denied.text
    assert reads == [ref.key] * 4


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
async def test_app_reference_survives_tail_restore_and_page_dependencies(runtime, tenant, count, monkeypatch):
    from a13n_service.runs import checkpoints
    from a13n_service.runs.attempts import Lease
    from a13n_service.runs.display import Page, Snapshot, Tail

    from .test_display_contents import item

    lease = Lease("run_apps", "rat_apps", "thr_apps", tenant.organization_id, tenant.workspace_id, 1, "w", "t")
    contents = Contents()
    for index in range(count):
        assert contents.app(f"app_{index}", b'{"original":true}', binding=app_binding(f"app_{index}"))
    value = item(
        [
            {"app_id": f"app_{index}", "unavailable": False, "resource_uri": "ui://" + "x" * 400}
            for index in range(count)
        ],
        "mcp_apps",
    )
    first = await checkpoints.publish_display(runtime, lease, Snapshot([], Tail(items=[value])), contents=contents)
    assert sum(ref.purpose == "app" for ref in first.tail.refs) == count
    reads = []
    get = runtime.objects.get

    async def counted_get(key):
        reads.append(key)
        return await get(key)

    monkeypatch.setattr(runtime.objects, "get", counted_get)
    tail = await checkpoints.load_tail(runtime.objects, first.tail, hydrate=True)
    app_keys = {ref.key for ref in first.tail.refs if ref.purpose == "app"}
    assert not app_keys.intersection(reads)
    assert tail.items[0].content == value.content
    restored = Contents(tail.items, tail.refs)
    second = await checkpoints.publish_display(runtime, lease, Snapshot([], tail), contents=restored)
    assert second.tail.refs == first.tail.refs
    original_ref = next(ref for ref in second.tail.refs if ref.purpose == "app")
    assert original_ref.app == app_binding(original_ref.id)
    assert await checkpoints.load(runtime.objects, original_ref) == b'{"original":true}'
    paged = await checkpoints.publish_display(
        runtime, lease, Snapshot([Page(items=tail.items)], Tail(first=2)), contents=restored
    )
    assert {ref.id for ref in paged.pages[0].refs} == {ref.id for ref in second.tail.refs}
    page = await checkpoints.load_page(runtime.objects, paged.pages[0])
    assert page.refs == second.tail.refs
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
    contents.app("app_one", b'{"original":true}', binding=app_binding("app_one"))
    value = item([{"app_id": "app_one", "unavailable": False}], "mcp_apps")
    for _ in range(2):
        saved = await contents.item(value, fail)
        assert saved.content["mcp_apps"] == [{"app_id": "app_one", "unavailable": True}]
        assert contents.dependencies([saved]) == ()
    assert attempts == 1 and not contents.pending_apps


def app_binding(identifier):
    connection_id = "con_" + "1" * 32
    return AppBinding(
        reference=AppReference(
            app_id=identifier,
            connection_id=connection_id,
            agent_revision_id="agr_test",
            source_run_id="run_child",
            tool_call_id="call_show",
            tool_name="show",
            resource_uri=URI,
        ),
        run_id="run_apps",
        connection_url="https://app.test/mcp",
        selection=ConnectionSelection(connection_id=connection_id, permissions={"reset": "ask"}),
    )


async def test_app_binding_size_limit_omits_publication(monkeypatch):
    from a13n_service.runs import contents as module

    binding = app_binding("app_one")
    size = len(binding.model_dump_json().encode())
    monkeypatch.setattr(module, "MAX_APP_BINDING_BYTES", size - 1)
    contents = Contents()
    assert not contents.app("app_one", b"{}", binding=binding)
    assert not contents.pending_apps and contents.pending_app_bytes == 0
    monkeypatch.setattr(module, "MAX_APP_BINDING_BYTES", size)
    assert contents.app("app_one", b"{}", binding=binding)


@pytest.mark.parametrize("app_run", [True], indirect=True)
async def test_original_mcp_error_is_retained_without_being_replayed(service, app_run):
    response = await service.client.post(app_run.path + "/open")
    assert response.status_code == 200, response.text
    assert response.json()["result"]["isError"] is True
    assert response.json()["result"]["_meta"]["private_app_value"] == "original-private-value"
    assert app_run.calls.show == 1
