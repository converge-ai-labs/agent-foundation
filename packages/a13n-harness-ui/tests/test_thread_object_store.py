from __future__ import annotations

import asyncio

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.objects import ObjectStoreError
from a13n_harness_ui.capability_runtime import production_run_bindings
from a13n_harness_ui.extensions import HarnessUiExtensionCatalog
from a13n_harness_ui.object_store import ThreadObjectStore, prune
from a13n_harness_ui.settings import ObjectStoreSettings
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

pytestmark = pytest.mark.anyio


async def test_persistent_library_lru_byte_quota_and_promotion(tmp_path):
    policy = ObjectStoreSettings(max_temporary_objects=2, max_temporary_bytes=8)
    store = ThreadObjectStore(tmp_path, "thread_one", policy=policy)
    first = await store.put(b"one", media_type="image/png")
    second = await store.put(b"two", media_type="audio/wav")
    assert await store.read(first.object_id) == b"one"
    third = await store.put(b"three", media_type="text/plain")
    with pytest.raises(ObjectStoreError, match="unavailable"):
        await store.info(second.object_id)
    assert {item.object_id for item in await store.list()} == {first.object_id, third.object_id}
    retained = await store.store(first.object_id)
    assert retained.object_id == first.object_id and retained.retention == "stored"
    await store.put(b"12345678", media_type="application/octet-stream")
    reopened = ThreadObjectStore(tmp_path, "thread_one", policy=policy)
    assert await reopened.read(first.object_id) == b"one"
    assert (await reopened.store(first.object_id)) == retained
    with pytest.raises(ObjectStoreError):
        await ThreadObjectStore(tmp_path, "thread_two").read(first.object_id)
    await reopened.delete(first.object_id)
    with pytest.raises(ObjectStoreError):
        await store.read(first.object_id)


async def test_idle_ttl_read_refresh_and_inactive_thread_cleanup(tmp_path, monkeypatch):
    current = [0.0]
    monkeypatch.setattr("a13n_harness_ui.object_store.time.time", lambda: current[0])
    policy = ObjectStoreSettings(temporary_ttl_seconds=10)
    store = ThreadObjectStore(tmp_path, "thread_one", policy=policy)
    refreshed = await store.put(b"read", media_type="image/png")
    observed = await store.put(b"info", media_type="image/png")
    retained = await store.put(b"stored", media_type="image/png")
    await store.store(retained.object_id)
    current[0] = 9
    await store.info(observed.object_id)
    await store.list()
    assert await store.read(refreshed.object_id) == b"read"
    current[0] = 11
    await prune(tmp_path, policy)
    with pytest.raises(ObjectStoreError):
        await store.store(observed.object_id)
    assert await store.read(refreshed.object_id) == b"read"
    current[0] = 22
    await prune(tmp_path, policy)
    assert [item.object_id for item in await store.list()] == [retained.object_id]
    assert not (tmp_path / "object-library" / "thread_one" / refreshed.object_id).exists()
    assert await store.read(retained.object_id) == b"stored"


async def test_concurrent_instances_preserve_catalog_and_reclaim_orphans(tmp_path):
    stores = [ThreadObjectStore(tmp_path, "thread_one") for _ in range(10)]
    items = await asyncio.gather(
        *(store.put(bytes([i]), media_type="application/octet-stream") for i, store in enumerate(stores))
    )
    assert len(await stores[0].list()) == 10
    assert await asyncio.gather(*(store.read(item.object_id) for store, item in zip(stores, items, strict=True))) == [
        bytes([i]) for i in range(10)
    ]
    orphan = tmp_path / "object-library" / "thread_one" / "obj_orphan"
    orphan.write_bytes(b"interrupted publication")
    await prune(tmp_path, ObjectStoreSettings())
    assert not orphan.exists()


async def test_production_selected_capability_and_host_binding(tmp_path):
    # Exercise the actual catalog selection and Host binding, not a replacement Store.
    from a13n_harness_ui.configuration.models import ProcessConfiguration

    selected = HarnessUiExtensionCatalog().capabilities((("object_store", {}),))
    policy = ProcessConfiguration.model_validate({"object_store": {"temporary_ttl_seconds": 172800}}).object_store
    bindings = production_run_bindings(
        RunBindings.embedded(),
        {"a13n.object-store"},
        data_root=tmp_path,
        thread_id="thread_one",
        object_store_policy=policy,
    )
    assert isinstance(bindings.object_store, ThreadObjectStore)
    # The native input path uses this same binding and persists across fresh Runs.
    info = await bindings.object_store.put(b"exact bytes", media_type="image/png")

    async def stream(messages, agent_info):
        if not any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            yield {0: DeltaToolCall(name="asset_store", json_args='{"ref": "' + info.ref + '"}', tool_call_id="store")}
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(selected[0].capability,),
    )
    result = await executable.run("keep it", bindings=bindings)
    assert result.output_or_raise() == "done"
    reopened = ThreadObjectStore(tmp_path, "thread_one")
    assert (await reopened.info(info.object_id)).retention == "stored"
    assert await reopened.read(info.object_id) == b"exact bytes"


@pytest.mark.usefixtures("migrated_databases")
async def test_app_root_execution_uses_host_store_and_preserves_it_on_restart(tmp_path, monkeypatch):
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver

    from .test_app import _settings, _write_configuration

    root = _write_configuration(tmp_path)
    agent = tmp_path / "agents" / "assistant.yaml"
    agent.write_text(agent.read_text() + "\ncapabilities:\n  - capability: object_store\n")
    observed = []

    async def stream(messages, info):
        returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if not returns:
            yield {0: DeltaToolCall(name="asset_list", json_args="{}", tool_call_id="list")}
        else:
            observed.extend(returns)
            yield "listed"

    async def resolve(self, model_id, *, thread_id):
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    settings = _settings(tmp_path / "state")
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        thread = await app.create_thread(title="Private media")
        private = ThreadObjectStore(settings.storage.data_root, thread.thread_id)
        item = await private.put(b"persist", media_type="image/png")
        await private.store(item.object_id)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="List my objects")
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.outcome is not None, operation
        assert operation.outcome.execution.output == "listed", operation
        assert item.ref in str(observed)
    async with open_harness_ui_app(settings, configuration_path=root):
        reopened = ThreadObjectStore(settings.storage.data_root, thread.thread_id)
        assert await reopened.read(item.object_id) == b"persist"


@pytest.mark.parametrize("lifetime", ["run", "host", "apps"])
@pytest.mark.parametrize("server_default,override", [(True, None), (False, True), (True, False), (False, None)])
async def test_mcp_object_ref_opt_in_and_tool_override(tmp_path, server_default, override, lifetime):
    import base64
    import json

    from a13n_harness.capabilities import ObjectStoreCapability
    from a13n_harness_ui.composition.models import ResolvedMcpRecipe
    from a13n_harness_ui.configuration.models import McpRemoteTransport
    from a13n_harness_ui.mcp_adapters import HarnessUiMCP
    from a13n_harness_ui.mcp_apps.connections import Connections as AppConnections
    from a13n_harness_ui.mcp_runtime.connections import Connections
    from fastmcp import FastMCP

    from .test_tool_search_integration import _http_mcp

    server, received = FastMCP("Exact object transport"), []

    @server.tool
    def send(data_base64: str) -> str:
        received.append(base64.b64decode(data_base64))
        return "sent"

    store = ThreadObjectStore(tmp_path, "thread_one")
    item = await store.put(b"\x00real MCP payload\xff", media_type="image/png")
    step = 0

    async def model(messages, info):
        nonlocal step
        step += 1
        if step == 1:
            yield {0: DeltaToolCall(name="send", json_args=json.dumps({"data_base64": item.ref}), tool_call_id="send")}
        else:
            yield "done"

    connections = AppConnections() if lifetime == "apps" else Connections() if lifetime == "host" else None
    async with _http_mcp(server) as url:
        try:
            recipe = ResolvedMcpRecipe(
                server_id="mcp-media",
                transport=McpRemoteTransport(url=url),
                supports_object_refs=server_default,
                object_ref_tools={} if override is None else {"send": override},
                on_error="raise",
                host_owned=lifetime == "host",
                apps_enabled=lifetime == "apps",
            )
            executable = HarnessBuilder().build(
                AgentSpec(),
                model=FunctionModel(stream_function=model),
                output_type=str,
                capabilities=[
                    ObjectStoreCapability(),
                    HarnessUiMCP(
                        recipe,
                        apps=connections if lifetime == "apps" else None,
                        connections=connections if lifetime == "host" else None,
                    ),
                ],
            )
            result = await executable.run("send it", bindings=RunBindings.embedded(object_store=store))
        finally:
            if connections is not None:
                await connections.close()
    assert result.output_or_raise() == "done"
    assert received == ([b"\x00real MCP payload\xff"] if (server_default if override is None else override) else [])
    assert "real MCP payload" not in str(result.all_messages())


async def test_deleted_thread_library_reclaims_stored_bytes(tmp_path):
    store = ThreadObjectStore(tmp_path, "thread_deleted")
    item = await store.put(b"retained", media_type="image/png")
    await store.store(item.object_id)

    async def exists(thread_id):
        assert thread_id == "thread_deleted"
        return False

    await prune(tmp_path, ObjectStoreSettings(), exists=exists)
    assert await store.list() == []
    assert not (tmp_path / "object-library" / "thread_deleted" / item.object_id).exists()
