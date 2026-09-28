from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from a13n_harness.tools.identity import ToolIdentity
from a13n_harness_ui.composition import AgentCompositionResolver, CompositionAcceptanceService
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.mcp_adapters import prepare_mcp_transport
from a13n_harness_ui.mcp_apps.connections import CapturedCall, Connections
from a13n_harness_ui.mcp_apps.messages import AppMessageRequest
from a13n_harness_ui.mcp_apps.operations import AppOperations, AppToolRequest
from a13n_harness_ui.mcp_apps.owners import CurrentOwners
from a13n_harness_ui.mcp_apps.snapshots import AppSnapshots
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import AgentResourceSource, ObjectKind, ThreadConfiguration, open_local_store
from a13n_harness_ui.surfaces import RootRunReceipt
from mcp.types import TextContent

from .test_composition import _catalog, _selection, _write_source
from .test_mcp_apps_connections import _SERVER
from .test_thread_repository import _initial

pytestmark = pytest.mark.anyio


@pytest.fixture
async def apps(tmp_path: Path):
    config = _write_source(tmp_path)
    root = yaml.safe_load(config.read_text())
    root["webui"] = {"mcp_apps": {"enabled": True, "servers": ["mcp-docs"]}}
    config.write_text(yaml.safe_dump(root))
    script = tmp_path / "apps_server.py"
    script.write_text(_SERVER)
    mcp = tmp_path / "mcp/docs.yaml"
    server = yaml.safe_load(mcp.read_text())
    server["transport"] = {"command": sys.executable, "arguments": [str(script)]}
    mcp.write_text(yaml.safe_dump(server))
    source = await load_harness_ui_configuration(config)
    resolver = AgentCompositionResolver(_catalog(), host_mode="webui")
    async with open_local_store(StorageSettings(data_root=tmp_path / "state")) as store:
        configurations = CompositionAcceptanceService(store, resolver)
        await configurations.accept(source, expected_current_digest=None)
        selection = _selection()
        await store.threads.create(
            thread_id="thread-1",
            configuration=ThreadConfiguration(
                version=1,
                project_id=selection.project_id,
                agent_source=AgentResourceSource(id=selection.agent_source_id),
                environment_profile_id=selection.environment_profile_id,
                harness_plugin_ids=selection.harness_plugin_ids,
                mcp_server_ids=selection.mcp_server_ids,
            ),
            initial_state=_initial(),
        )
        owners = CurrentOwners(store, configurations, resolver)
        connections = Connections()
        snapshots = AppSnapshots(store.objects, connections)
        operations = AppOperations(snapshots, owners, tmp_path)
        try:
            owner = await owners.resolve("thread-1")
            recipe = owner.recipe("mcp-docs")
            transport, effective, _ = await prepare_mcp_transport(recipe, tmp_path)
            connection = await connections.acquire(
                "thread-1", "mcp-docs", effective, transport, binding=recipe.transport.model_dump_json()
            )
            tool = next(tool for tool in await connection.client.list_tools() if tool.name == "counter")
            result = await connection.client.call_tool_mcp("counter", {})
            reference = await snapshots.capture(
                CapturedCall(connection, tool, {}, result),
                thread_id="thread-1",
                run_id="run-original",
                call_id="call-original",
            )
            yield operations, reference
        finally:
            await operations.close()
            await connections.close()


async def _policy(operations: AppOperations, mode: str, *, tools=None, review=None) -> None:
    path = operations.configuration_root / "agents/assistant.yaml"
    agent = yaml.safe_load(path.read_text())
    permissions = {"rules": {"mcp/mcp-docs/counter": mode, "mcp/mcp-docs/reset": mode}}
    if review is not None:
        permissions["review"] = review
    agent["capabilities"] = [{"capability": "ToolPermissionsCapability", "configuration": permissions}]
    agent["tools"] = tools
    path.write_text(yaml.safe_dump(agent))
    await _accept(operations)


async def _accept(operations: AppOperations) -> None:
    previous = await operations.owners.configurations.current()
    source = await load_harness_ui_configuration(operations.configuration_root / "a13n-harness-ui.yaml")
    await operations.owners.configurations.accept(source, expected_current_digest=previous.source_digest)


async def _settle(operations: AppOperations, view_id: str, key: str):
    async with asyncio.timeout(10):
        while True:
            operation = operations.get_operation("thread-1", view_id, key)
            if operation.status not in {"checking", "running"}:
                return operation
            await asyncio.wait(tuple(operations._tasks), return_when=asyncio.FIRST_COMPLETED)


async def test_views_share_session_not_followup_results_and_requests_are_single_consumption(apps) -> None:
    operations, reference = apps
    first = await operations.activate(reference)
    second = await operations.activate(reference)
    assert first.view_id != second.view_id
    assert first.connection_generation == second.connection_generation
    request = AppToolRequest(request_key="one", name="counter")
    await operations.call_tool("thread-1", first.view_id, request)
    result = await _settle(operations, first.view_id, "one")
    assert result.status == "completed"
    assert result.result["structuredContent"]["count"] == 2  # Activation did not replay the original.
    assert (await operations.call_tool("thread-1", first.view_id, request)) == result
    with pytest.raises(HarnessUiError, match="different arguments"):
        await operations.call_tool("thread-1", first.view_id, request.model_copy(update={"arguments": {"delay": 0}}))
    with pytest.raises(HarnessUiError, match="unavailable"):
        operations.get_operation("thread-1", second.view_id, "one")
    original = await operations.snapshots.read(reference)
    assert original.snapshot.result["structuredContent"]["count"] == 1
    operations.close_view("thread-1", first.view_id)
    assert operations.snapshots.connections.get("thread-1", "mcp-docs").connected
    await operations.call_tool("thread-1", second.view_id, request)
    assert (await _settle(operations, second.view_id, "one")).result["structuredContent"]["count"] == 3


async def test_current_policy_and_schema_reject_before_business_dispatch(apps) -> None:
    operations, reference = apps
    view = await operations.activate(reference)
    await operations.call_tool(
        "thread-1", view.view_id, AppToolRequest(request_key="bad", name="counter", arguments={"delay": "wrong"})
    )
    assert (await _settle(operations, view.view_id, "bad")).status == "failed"
    await _policy(operations, "deny")
    await operations.call_tool("thread-1", view.view_id, AppToolRequest(request_key="denied", name="counter"))
    assert (await _settle(operations, view.view_id, "denied")).status == "failed"
    await _policy(operations, "allow")
    await operations.call_tool("thread-1", view.view_id, AppToolRequest(request_key="ok", name="counter"))
    assert (await _settle(operations, view.view_id, "ok")).result["structuredContent"]["count"] == 2


async def test_approval_is_bound_consumed_once_and_rechecked_against_current_policy(apps) -> None:
    operations, reference = apps
    await _policy(operations, "ask")
    view = await operations.activate(reference)
    await operations.call_tool("thread-1", view.view_id, AppToolRequest(request_key="ask", name="counter"))
    pending = await _settle(operations, view.view_id, "ask")
    assert pending.status == "approval_required"
    assert pending.tool_id == "mcp/mcp-docs/counter"
    connection = operations.snapshots.connections.get("thread-1", "mcp-docs")
    async with connection.dispatch:
        operations.decide("thread-1", view.view_id, "ask", approve=True)
        with pytest.raises(HarnessUiError, match="no longer pending"):
            operations.decide("thread-1", view.view_id, "ask", approve=True)
        # The approval has been consumed, but another admitted call still owns the lane.
        await _policy(operations, "deny")
    denied = await _settle(operations, view.view_id, "ask")
    assert denied.status == "failed"
    await _policy(operations, "ask")
    await operations.call_tool("thread-1", view.view_id, AppToolRequest(request_key="accepted", name="counter"))
    assert (await _settle(operations, view.view_id, "accepted")).status == "approval_required"
    operations.decide("thread-1", view.view_id, "accepted", approve=True)
    assert (await _settle(operations, view.view_id, "accepted")).result["structuredContent"]["count"] == 2


async def test_close_invalidates_pending_approval_without_closing_session(apps) -> None:
    operations, reference = apps
    await _policy(operations, "ask")
    view = await operations.activate(reference)
    await operations.call_tool("thread-1", view.view_id, AppToolRequest(request_key="ask", name="counter"))
    assert (await _settle(operations, view.view_id, "ask")).status == "approval_required"
    operations.close_view("thread-1", view.view_id)
    with pytest.raises(HarnessUiError, match="closed"):
        operations.decide("thread-1", view.view_id, "ask", approve=True)
    assert operations.get_operation("thread-1", view.view_id, "ask").status == "denied"
    assert operations.snapshots.connections.get("thread-1", "mcp-docs").connected


async def test_app_only_tools_still_obey_host_allowlist(apps) -> None:
    operations, reference = apps
    view = await operations.activate(reference)
    await operations.call_tool("thread-1", view.view_id, AppToolRequest(request_key="reset", name="reset"))
    assert (await _settle(operations, view.view_id, "reset")).status == "completed"
    await _policy(operations, "allow", tools=["counter"])
    await operations.call_tool("thread-1", view.view_id, AppToolRequest(request_key="excluded", name="reset"))
    assert (await _settle(operations, view.view_id, "excluded")).status == "failed"


async def _child(operations: AppOperations):
    owners = operations.owners
    root = await owners.resolve("thread-1")
    edge = next(edge for edge in root.node.children if edge.source_kind == "markdown")
    source = await owners.configurations.current()
    composition = owners.resolver.resolve_run(source, _selection()).model_copy(
        update={"thread_id": "child", "root": edge.definition}
    )
    saved = await owners.store.objects.publish_model(object_kind=ObjectKind.run_composition, value=composition)
    thread = await owners.store.threads.get("thread-1")
    await owners.store.threads.create(
        thread_id="child", parent_thread_id="thread-1", configuration=thread.configuration, initial_state=_initial()
    )
    await owners.store.child_executions.create(
        execution_id="execution-child",
        parent_thread_id="thread-1",
        child_thread_id="child",
        child_run_id="run-child",
        run_composition=saved.ref,
    )
    assert (await owners.resolve("child")).route == (edge.name,)
    return edge


@pytest.mark.parametrize("keep_generic", [False, True])
async def test_app_selection_is_independent_of_generic_root_and_child_selection(apps, keep_generic) -> None:
    from a13n_harness_ui.composition import ThreadCompositionSelection

    operations, reference = apps
    owners = operations.owners
    connections = operations.snapshots.connections
    connection = connections.get("thread-1", "mcp-docs")
    thread = await owners.store.threads.get("thread-1")
    if not keep_generic:
        thread = await owners.store.threads.update_configuration(
            thread_id=thread.thread_id,
            expected_version=thread.configuration.version,
            replacement=thread.configuration.model_copy(update={"version": 2, "mcp_server_ids": ()}),
        )
    await owners.retire_unselected(connections)
    assert not connection.retired
    view = await operations.activate(reference)
    assert view.connection_generation == connection.generation
    await _child(operations)
    assert (await owners.resolve("child")).recipe("mcp-docs").apps_enabled

    path = operations.configuration_root / "a13n-harness-ui.yaml"
    document = yaml.safe_load(path.read_text())
    document["webui"]["mcp_apps"]["servers"] = []
    path.write_text(yaml.safe_dump(document))
    await _accept(operations)
    await owners.retire_unselected(connections)
    assert connection.retired
    with pytest.raises(HarnessUiError, match="not enabled"):
        (await owners.resolve("child")).recipe("mcp-docs")
    with pytest.raises(HarnessUiError, match="not enabled"):
        await operations.activate(reference)
    source = await owners.configurations.current()
    node = owners.resolver.resolve_agent(source, ThreadCompositionSelection.from_thread(thread))
    assert tuple(item.server_id for item in node.mcp_servers) == (("mcp-docs",) if keep_generic else ())
    assert all(not item.apps_enabled for item in node.mcp_servers)


async def test_current_child_route_uses_current_parent_policy_and_never_historical_fallback(apps) -> None:
    operations, _ = apps
    await _child(operations)
    owners = operations.owners
    await _policy(operations, "deny")
    child = await owners.resolve("child")
    assert owners.permissions(child).permissions.resolve(ToolIdentity("mcp/mcp-docs/counter")) == "deny"
    agent_path = operations.configuration_root / "agents/assistant.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent["subagents"] = []
    agent_path.write_text(yaml.safe_dump(agent))
    await _accept(operations)
    with pytest.raises(HarnessUiError, match="no longer selected"):
        await owners.resolve("child")


@pytest.mark.parametrize("configured", [False, True])
async def test_app_review_policy_never_resolves_or_calls_a_model(apps, monkeypatch, configured) -> None:
    from unittest.mock import AsyncMock

    from a13n_harness.capabilities.tool_review import AgentToolReviewer
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver

    operations, reference = apps
    resolve = AsyncMock(side_effect=AssertionError("App operations must not resolve a model"))
    review = AsyncMock(side_effect=AssertionError("App operations must not invoke model review"))
    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    monkeypatch.setattr(AgentToolReviewer, "review", review)
    await _policy(
        operations,
        "review",
        review={"model": "model-primary", "on_error": "deny", "on_flagged": "deny"} if configured else None,
    )
    before = await operations.owners.store.threads.get("thread-1")
    view = await operations.activate(reference)
    await operations.call_tool("thread-1", view.view_id, AppToolRequest(request_key="review", name="counter"))
    result = await _settle(operations, view.view_id, "review")
    assert result.status == "completed"
    assert result.result["structuredContent"]["count"] == 2
    assert "review_usage" not in result.model_dump()
    assert "review_usage" not in result.model_json_schema()["properties"]
    assert await operations.owners.store.threads.get("thread-1") == before
    resolve.assert_not_called()
    review.assert_not_called()


async def test_explicit_reactivation_does_not_recover_private_state_or_reauthorize_old_views(apps) -> None:
    operations, reference = apps
    first = await operations.activate(reference)
    await operations.snapshots.connections.get("thread-1", "mcp-docs").close()
    second = await operations.activate(reference)
    assert first.connection_generation != second.connection_generation
    await operations.call_tool("thread-1", first.view_id, AppToolRequest(request_key="old", name="counter"))
    assert (await _settle(operations, first.view_id, "old")).status == "failed"
    await operations.call_tool("thread-1", second.view_id, AppToolRequest(request_key="new", name="counter"))
    assert (await _settle(operations, second.view_id, "new")).result["structuredContent"]["count"] == 1


async def test_activation_rejects_changed_original_resource(apps, monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock

    from a13n_harness_ui.mcp_apps.connections import MIME_TYPE
    from mcp.types import ReadResourceResult, TextResourceContents

    operations, reference = apps
    connection = operations.snapshots.connections.get("thread-1", "mcp-docs")
    monkeypatch.setattr(
        connection.client,
        "read_resource_mcp",
        AsyncMock(
            return_value=ReadResourceResult(
                contents=[TextResourceContents(uri="ui://counter/app.html", mime_type=MIME_TYPE, text="<p>Changed</p>")]
            )
        ),
    )
    with pytest.raises(HarnessUiError, match="resource changed"):
        await operations.activate(reference)
    assert not operations._views


async def test_retained_operation_data_is_bounded_and_closed_views_release_capacity(
    apps, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a13n_harness_ui.mcp_apps import operations as module

    operations, reference = apps
    first = await operations.activate(reference)
    bound = operations._retained_bytes + 64
    with monkeypatch.context() as patch:
        patch.setattr(module, "_MAX_RETAINED_BYTES", bound)
        with pytest.raises(HarnessUiError, match="retained operation data"):
            await operations.call_tool(
                "thread-1", first.view_id, AppToolRequest(request_key="no-space", name="counter")
            )
        assert operations._retained_bytes <= bound
    await operations.call_tool("thread-1", first.view_id, AppToolRequest(request_key="ok", name="counter"))
    assert (await _settle(operations, first.view_id, "ok")).result["structuredContent"]["count"] == 2
    retained = operations._retained_bytes
    await asyncio.gather(*operations._tasks)
    operations.close_view("thread-1", first.view_id)
    await operations.activate(reference)
    assert operations._retained_bytes < retained


async def test_effective_credential_change_rejects_existing_view_without_auto_reconnect(
    apps, monkeypatch: pytest.MonkeyPatch
) -> None:
    operations, reference = apps
    server_path = operations.configuration_root / "mcp/docs.yaml"
    server = yaml.safe_load(server_path.read_text())
    server["transport"]["environment"] = {"APP_CREDENTIAL": {"env": "APP_CREDENTIAL"}}
    server_path.write_text(yaml.safe_dump(server))
    monkeypatch.setenv("APP_CREDENTIAL", "first")
    await _accept(operations)
    view = await operations.activate(reference)
    monkeypatch.setenv("APP_CREDENTIAL", "changed")
    await operations.call_tool("thread-1", view.view_id, AppToolRequest(request_key="changed", name="counter"))
    failed = await _settle(operations, view.view_id, "changed")
    assert failed.status == "failed" and "binding changed" in failed.reason
    assert operations.snapshots.connections.get("thread-1", "mcp-docs").generation == view.connection_generation


@pytest.mark.parametrize("mode", ["allow", "review"])
@pytest.mark.parametrize("revoke", ["view", "policy"])
async def test_revocation_while_dispatch_enumerates_tools_prevents_business_call(
    apps, monkeypatch: pytest.MonkeyPatch, revoke: str, mode: str
) -> None:
    operations, reference = apps
    await _policy(operations, mode)
    view = await operations.activate(reference)
    connection = operations.snapshots.connections.get("thread-1", "mcp-docs")
    list_tools = connection.client.list_tools
    enumerating = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def paused(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            enumerating.set()
            await release.wait()
        return await list_tools(*args, **kwargs)

    monkeypatch.setattr(connection.client, "list_tools", paused)
    await operations.call_tool("thread-1", view.view_id, AppToolRequest(request_key="revoked", name="counter"))
    try:
        async with asyncio.timeout(10):
            await enumerating.wait()
        if revoke == "view":
            operations.close_view("thread-1", view.view_id)
        else:
            await _policy(operations, "deny")
    finally:
        release.set()
    await asyncio.gather(*operations._tasks)
    result = operations.get_operation("thread-1", view.view_id, "revoked")
    assert result.status in {"failed", "denied"}
    assert (await connection.client.call_tool_mcp("counter", {})).structured_content["count"] == 2


@pytest.mark.parametrize("change", ["deny", "ask", "review_settings", "allow"])
async def test_queued_app_rechecks_policy_but_ignores_model_review_settings(apps, monkeypatch, change) -> None:
    operations, reference = apps
    await _policy(operations, "review", review={"model": "model-primary", "on_error": "deny"})
    view = await operations.activate(reference)
    connection = operations.snapshots.connections.get("thread-1", "mcp-docs")
    queued = asyncio.Event()
    call_app_tool = connection.client.call_app_tool

    async def notify_queued(*args, **kwargs):
        queued.set()
        return await call_app_tool(*args, **kwargs)

    monkeypatch.setattr(connection.client, "call_app_tool", notify_queued)
    async with connection.dispatch:
        await operations.call_tool("thread-1", view.view_id, AppToolRequest(request_key="queued", name="counter"))
        async with asyncio.timeout(10):
            await queued.wait()
        if change == "review_settings":
            path = operations.configuration_root / "models/primary.yaml"
            model = yaml.safe_load(path.read_text())
            model["settings"] = {"temperature": 0.5}
            path.write_text(yaml.safe_dump(model))
            await _policy(operations, "review", review={"model": "model-primary", "on_error": "allow"})
        else:
            await _policy(operations, change)
    result = await _settle(operations, view.view_id, "queued")
    if change in {"deny", "ask"}:
        assert result.status == "failed" and result.result is None
        assert (await connection.client.call_tool_mcp("counter", {})).structured_content["count"] == 2
    else:
        assert result.status == "completed"
        assert result.result["structuredContent"]["count"] == 2


@pytest.mark.parametrize("configured", [False, True])
async def test_app_operations_do_not_invoke_custom_run_reviewers(apps, monkeypatch, configured):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from a13n_harness.capabilities.tool_review import ToolReviewConfig
    from a13n_harness.tools.permissions import ToolPermissions, ToolPermissionsCapability

    operations, reference = apps
    await _policy(operations, "review", review={"model": "model-primary"} if configured else None)
    custom = AsyncMock(side_effect=AssertionError("Run-bound reviewer must not run"))
    policy = ToolPermissionsCapability(
        ToolPermissions(default="review"),
        review=ToolReviewConfig(model="model-primary", on_error="allow") if configured else None,
        reviewer=SimpleNamespace(review=custom),
    )
    monkeypatch.setattr(operations.owners, "permissions", lambda owner: policy)
    view = await operations.activate(reference)
    await operations.call_tool("thread-1", view.view_id, AppToolRequest(request_key="custom", name="counter"))
    result = await _settle(operations, view.view_id, "custom")
    assert result.status == "completed"
    assert result.result["structuredContent"]["count"] == 2
    custom.assert_not_called()


async def test_child_message_is_attributed_to_root_and_rechecks_route_before_submission(apps) -> None:
    operations, _ = apps
    child_source = operations.configuration_root / "subagents/explorer.md"
    child_source.write_text(child_source.read_text().replace("tools: [glob, grep]", "tools: [glob, grep, counter]"))
    await _accept(operations)
    edge = await _child(operations)
    owner = await operations.owners.resolve("child")
    recipe = owner.recipe("mcp-docs")
    transport, effective, _ = await prepare_mcp_transport(recipe, operations.configuration_root)
    connection = await operations.snapshots.connections.acquire(
        "child", "mcp-docs", effective, transport, binding=recipe.transport.model_dump_json()
    )
    tool = next(item for item in await connection.client.list_tools() if item.name == "counter")
    result = await connection.client.call_tool_mcp("counter", {})
    reference = await operations.snapshots.capture(
        CapturedCall(connection, tool, {}, result), thread_id="child", run_id="run-child", call_id="child-call"
    )
    view = await operations.activate(reference)
    submitted = []
    preparing = asyncio.Event()
    prepared = asyncio.Event()

    async def submit(value, parts):
        preparing.set()
        await prepared.wait()
        # Ordinary input preparation happens before this final Host authorization.
        await operations.authorize_message(value)
        submitted.append((value.root_thread_id, parts))
        return RootRunReceipt(receipt_id="receipt-child", thread_id="thread-1", submitted_at=datetime.now(UTC))

    request = AppMessageRequest(request_key="first", content=(TextContent(type="text", text="Review this selection"),))
    operations.send_message("child", view.view_id, request, submit)
    await preparing.wait()
    prepared.set()
    await asyncio.gather(*tuple(operations._tasks))
    accepted = operations.get_message("child", view.view_id, "first")
    assert accepted.status == "accepted"
    assert submitted[0][0] == "thread-1"
    assert "attributed handoff from a child App" in submitted[0][1][0]
    assert edge.name in submitted[0][1][0]
    assert submitted[0][1][1] == "Review this selection"
    assert operations.send_message("child", view.view_id, request, submit) == accepted
    assert len(submitted) == 1

    preparing.clear()
    prepared.clear()
    operations.send_message("child", view.view_id, request.model_copy(update={"request_key": "second"}), submit)
    await preparing.wait()
    path = operations.configuration_root / "agents/assistant.yaml"
    agent = yaml.safe_load(path.read_text())
    agent["subagents"] = []
    path.write_text(yaml.safe_dump(agent))
    await _accept(operations)
    prepared.set()
    await asyncio.gather(*tuple(operations._tasks))
    rejected = operations.get_message("child", view.view_id, "second")
    assert rejected.status == "failed"
    assert "no longer selected" in rejected.reason
    assert len(submitted) == 1
