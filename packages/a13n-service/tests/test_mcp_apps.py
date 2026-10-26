"""MCP App originals cross worker persistence and independent, authorized control requests."""

import asyncio
import json
from contextlib import contextmanager
from contextvars import ContextVar
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
from sqlalchemy import event

from .test_connections import admin, create, patch, serve

pytestmark = pytest.mark.anyio
URI = "ui://counter/app.html"
MIME = "text/html;profile=mcp-app"


@pytest.mark.parametrize("lost_boundary", [False, True])
async def test_app_ready_waits_for_commit_and_opens_before_run_ends(
    service, scripted_model, runs_kit, listen, monkeypatch, lost_boundary
):
    from a13n_service.runs import checkpoints
    from a13n_service.runs.stream import ThreadStream

    commit_entered, allow_commit, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    commit = checkpoints.commit

    async def held_commit(*args, **kwargs):
        result = await commit(*args, **kwargs)
        if any(ref.purpose == "app" for ref in kwargs["display"].tail.refs):
            commit_entered.set()
            await allow_commit.wait()  # Hold the test transaction after writing, before its caller commits.
        return result

    monkeypatch.setattr(checkpoints, "commit", held_commit)
    if lost_boundary:
        monkeypatch.setattr(ThreadStream, "boundary", lambda *args, **kwargs: None)
    server = FastMCP("Ready App")

    @server.tool(meta={"ui": {"resourceUri": URI}})
    def show_ready() -> str:
        return "Ready"

    @server.resource(URI, mime_type=MIME)
    def resource() -> str:
        return "<p>Ready</p>"

    async with serve(server.http_app(path="/mcp")) as mcp_url:
        connection = await create(service, {"config": {"url": mcp_url + "/mcp"}})
        agent = await runs_kit.create_agent(
            service,
            scripted_model,
            connection_tools=[{"connection_id": connection["id"]}],
            toolsets={"files": {"enabled": False}, "shell": {"enabled": False}},
        )
        scripted_model.call("show_ready", {}, call_id="call_ready")
        scripted_model.say("Finished", gate=finish)
        submitted = await runs_kit.start_thread(service, agent, "Show the App")
        run_id = submitted["run"]["id"]
        headers = await runs_kit.bearer(service)
        async with listen(service.app) as base:
            url = f"{base}{service.api}/sessions/{submitted['thread']['session_id']}/stream"
            async with runs_kit.frames(url, headers) as stream:
                await runs_kit.until(stream, lambda frame: frame[0] == "items")
                task = await runs_kit.attempt(service)
                try:
                    observed = await runs_kit.until(
                        stream,
                        lambda frame: frame[0] == "delta" and frame[2]["event"].get("name") == "a13n.mcp_apps",
                    )
                    assert not any(frame[0] == "app_ready" for frame in observed)
                    reference = observed[-1][2]["event"]["value"]["event"]["apps"][0]
                    path = f"{service.api}/runs/{run_id}/apps/{reference['app_id']}/open"
                    async with asyncio.timeout(10):
                        await commit_entered.wait()
                    assert (await service.client.post(path)).status_code == 404
                    # A database refresh while the write is uncommitted cannot announce readiness either.
                    from a13n_service.runs.stream import SessionHub

                    assert not (await SessionHub(service.runtime).base(run_id, 1)).apps
                    allow_commit.set()
                    ready = await runs_kit.until(stream, lambda frame: frame[0] == "app_ready")
                    assert ready[-1][2] == {
                        "thread_id": submitted["thread"]["id"],
                        "run_id": run_id,
                        "apps": [reference],
                    }
                    assert (await service.client.post(path)).status_code == 200
                    assert not task.done(), "Readiness must not wait for Run completion"
                    async with runs_kit.frames(url, headers) as reconnected:
                        replay = await runs_kit.until(reconnected, lambda frame: frame[0] == "app_ready")
                        assert replay[-1][2] == ready[-1][2]
                    finish.set()
                    await runs_kit.until(stream, lambda frame: frame[0] == "items" and frame[2]["complete"])
                    event, _, final_ready = await anext(stream)
                    assert event == "app_ready" and final_ready == ready[-1][2]
                finally:
                    allow_commit.set()
                    finish.set()
                    await task


@pytest.mark.parametrize("app_run", [False, "paged", "save_failed"], indirect=True)
async def test_saved_app_readiness_uses_committed_references(service, app_run, monkeypatch):
    from a13n_service.runs.contents import ContentObject
    from a13n_service.runs.stream import SessionHub

    hub = SessionHub(service.runtime)
    read = hub._object

    async def display_only(ref):
        assert not isinstance(ref, ContentObject) or ref.purpose != "app", "Readiness must not download originals"
        return await read(ref)

    monkeypatch.setattr(hub, "_object", display_only)
    base = await hub.base(app_run.run_id, 1)
    assert app_run.reference["unavailable"] is app_run.save_failed
    expected = [] if app_run.reference["unavailable"] else [app_run.reference]
    assert [app.model_dump(mode="json") for app in base.apps] == expected


@pytest.fixture
async def app_run(service, scripted_model, runs_kit, request, monkeypatch):
    mode = getattr(request, "param", False)
    if mode == "save_failed":
        save_item = Contents.item

        async def failed_original(self, item, write):
            async def save(data):
                if b'"arguments"' in data and b'"result"' in data and b'"reference"' in data:
                    raise OSError("App original storage unavailable")
                return await write(data)

            return await save_item(self, item, save)

        monkeypatch.setattr(Contents, "item", failed_original)
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
            thread_id=submitted["thread"]["id"],
            run_id=run_id,
            reference=ref,
            calls=calls,
            connection=connection,
            agent=agent,
            history=history,
            paged=mode == "paged",
            save_failed=mode == "save_failed",
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
    from a13n_service.runs.stream import stream_key

    entries = await service.runtime.redis.xrange(stream_key(app_run.thread_id))
    events = [json.loads(fields["event"]) for _, fields in entries if "event" in fields]
    app_events = [event for event in events if event.get("name") == "a13n.mcp_apps"]
    assert len(app_events) == 1
    assert app_events[0]["type"] == "CUSTOM"
    assert app_events[0]["value"]["event"] == {"tool_call_id": "call_show", "apps": [app_run.reference]}
    assert "original-private-value" not in str(app_events)
    assert "html" not in app_events[0]["value"]["event"]
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


@pytest.mark.parametrize("extra_rounds", [0, 2])
async def test_app_dispatch_reuses_request_authorization(service, app_run, monkeypatch, extra_rounds):
    from a13n_service.runs.mcp_apps import service as apps
    from a13n_service.runs.mcp_apps.schemas import AppToolCall

    resolve, dispatch = apps._resolve, apps.call_app_tool
    resolutions, admitted = [], []

    async def resolve_once(*args, **kwargs):
        resolutions.append(kwargs.get("tool_name"))
        return await resolve(*args, **kwargs)

    async def continued(toolset, identity, check, arguments):
        # Exercise the same dispatch callback the SDK invokes for native continuation rounds.
        for _ in range(extra_rounds):
            await check(identity)
        return await dispatch(toolset, identity, check, arguments)

    async def proceed_app(session, call):
        admitted.append(call)

    monkeypatch.setattr(apps, "_resolve", resolve_once)
    monkeypatch.setattr(apps, "call_app_tool", continued)
    runtime = replace(service.runtime, admission=SimpleNamespace(proceed_app=proceed_app))
    with app_statements(runtime) as statements:
        result = await apps.call_tool(
            runtime,
            admin(service),
            service.tenant.workspace_id,
            app_run.run_id,
            app_run.reference["app_id"],
            AppToolCall(name="increment"),
        )
    print(
        f"App action: {len(statements)} SQL executions, {len(resolutions)} permission resolutions, {extra_rounds + 1} dispatch rounds"
    )
    assert result.result["structuredContent"] == {"count": 1}
    assert len(admitted) == extra_rounds + 1
    assert len({call.call_id for call in admitted}) == 1
    assert resolutions == ["increment"]
    assert sum("FROM runs" in statement for statement in statements) == 2
    assert sum("FROM workspaces" in statement for statement in statements) == 2
    assert len(statements) == 6 + extra_rounds


async def test_app_request_keeps_initial_grants_but_next_request_observes_revocation(service, app_run, monkeypatch):
    from a13n_service.infra.db import transaction
    from a13n_service.infra.ids import new_object_id
    from a13n_service.runs.mcp_apps import service as apps
    from a13n_service.tenancy.tables import GrantRow
    from sqlalchemy import delete

    resolve = apps._resolve
    resolved_count = 0

    async def revoke_after_resolve(*args, **kwargs):
        nonlocal resolved_count
        resolved = await resolve(*args, **kwargs)
        resolved_count += 1
        if resolved_count == 1:
            async with transaction(service.runtime.storage) as session:
                await session.execute(
                    delete(GrantRow).where(
                        GrantRow.principal_id == service.tenant.principal_id,
                        GrantRow.organization_id == service.tenant.organization_id,
                    )
                )
                session.add(
                    GrantRow(
                        id=new_object_id("gr"),
                        organization_id=service.tenant.organization_id,
                        workspace_id=None,
                        principal_id=service.tenant.principal_id,
                        role="viewer",
                        created_by_id=service.tenant.principal_id,
                    )
                )
        return resolved

    monkeypatch.setattr(apps, "_resolve", revoke_after_resolve)
    response = await service.client.post(app_run.path + "/tools/call", json={"name": "increment"})
    assert response.status_code == 200, response.text
    assert resolved_count == 1
    denied = await service.client.post(app_run.path + "/tools/call", json={"name": "increment"})
    assert denied.status_code == 403, denied.text
    assert app_run.calls.increment == 1


async def test_app_dispatch_still_refuses_a_changed_connection(service, app_run, monkeypatch):
    from a13n_service.runs.mcp_apps import service as apps

    dispatch = apps.call_app_tool

    async def change_before_dispatch(toolset, identity, check, arguments):
        await patch(service, app_run.connection, {"name": "Changed during App request"})
        return await dispatch(toolset, identity, check, arguments)

    monkeypatch.setattr(apps, "call_app_tool", change_before_dispatch)
    response = await service.client.post(app_run.path + "/tools/call", json={"name": "increment"})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "connection_changed"
    assert app_run.calls.increment == 0


@contextmanager
def app_statements(runtime):
    """Cursor executions for this operation and SDK child tasks, excluding fixture/background work and auth."""
    active = ContextVar("app_sql", default=False)
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        if active.get():
            statements.append(statement)

    engine = runtime.storage.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    token = active.set(True)
    try:
        yield statements
    finally:
        active.reset(token)
        event.remove(engine, "before_cursor_execute", record)


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


async def test_app_without_authorization_binding_is_rejected_without_object_reads(service, app_run, monkeypatch):
    from a13n_service.runs import runs

    runtime = service.runtime
    ref = await runs.content_ref(
        runtime, admin(service), service.tenant.workspace_id, app_run.run_id, app_run.reference["app_id"], purpose="app"
    )

    async def unbound_ref(*args, **kwargs):
        return ref.model_copy(update={"app": None})

    monkeypatch.setattr(runs, "content_ref", unbound_ref)
    reads = []
    get = runtime.objects.get

    async def counted_get(key):
        reads.append(key)
        return await get(key)

    monkeypatch.setattr(runtime.objects, "get", counted_get)
    opened = await service.client.post(app_run.path + "/open")
    assert opened.status_code == 404, opened.text
    called = await service.client.post(app_run.path + "/tools/call", json={"name": "increment"})
    assert called.status_code == 404, called.text
    resource = await service.client.post(app_run.path + "/resources/read", json={"uri": URI})
    assert resource.status_code == 404, resource.text
    assert reads == []


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
