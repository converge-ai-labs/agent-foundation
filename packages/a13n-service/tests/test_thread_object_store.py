"""Private library over real PostgreSQL metadata and the configured object backend."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_harness.objects import ObjectStoreError
from a13n_service.infra.db import now, transaction
from a13n_service.resources.assets.tables import AssetRow
from a13n_service.runs.attempts import LeaseLost
from a13n_service.runs.claim import claim
from a13n_service.runs.object_store import ThreadObjectStore, clean
from a13n_service.runs.tables import ThreadObjectRow
from a13n_service.tenancy.authorize import WorkspaceScope, execution_authority
from sqlalchemy import func, select

from .test_host_capabilities import admin

pytestmark = pytest.mark.anyio


async def setup_store(service, scripted_model, runs_kit, *, count=2, size=8):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    await runs_kit.start_thread(service, agent, "use private objects")
    (lease,) = await claim(service.runtime, worker_id="objects-test", worker_build="test", limit=1)
    actor = admin(service.tenant)
    authority = execution_authority(actor, WorkspaceScope(lease.organization_id, lease.workspace_id))
    settings = service.runtime.settings.model_copy(
        update={
            "object_store": service.runtime.settings.object_store.model_copy(
                update={"max_temporary_objects": count, "max_temporary_bytes": size}
            )
        }
    )
    runtime = replace(service.runtime, settings=settings)
    return ThreadObjectStore(runtime, lease, authority)


async def test_scoped_persistence_lru_promotion_and_no_workspace_publication(service, scripted_model, runs_kit):
    store = await setup_store(service, scripted_model, runs_kit)
    first = await store.put(b"one", media_type="image/png")
    second = await store.put(b"two", media_type="audio/wav")
    assert await store.read(first.object_id) == b"one"
    third = await store.put(b"three", media_type="text/plain")
    with pytest.raises(ObjectStoreError):
        await store.read(second.object_id)
    assert {item.object_id for item in await store.list()} == {first.object_id, third.object_id}
    retained = await store.store(first.object_id)
    assert retained.object_id == first.object_id and retained.retention == "stored"
    await store.put(b"12345678", media_type="application/octet-stream")
    reopened = ThreadObjectStore(store.runtime, store.lease, store.authority)
    assert await reopened.read(first.object_id) == b"one"
    assert await reopened.store(first.object_id) == retained
    async with transaction(store.runtime.storage) as session:
        assert await session.scalar(select(func.count()).select_from(AssetRow)) == 0
    await reopened.delete(first.object_id)
    with pytest.raises(ObjectStoreError):
        await store.read(first.object_id)
    # A copied reference alone never grants another Thread/attempt authority.
    foreign = ThreadObjectStore(store.runtime, replace(store.lease, thread_id="thr_foreign"), store.authority)
    with pytest.raises(LeaseLost):
        await foreign.read(third.object_id)


async def test_ttl_expiry_is_lazy_and_background_reclaims_inactive_threads(service, scripted_model, runs_kit):
    store = await setup_store(service, scripted_model, runs_kit, count=10, size=100)
    stale = await store.put(b"expired", media_type="image/png")
    refreshed = await store.put(b"read", media_type="image/png")
    retained = await store.put(b"keep", media_type="image/png")
    await store.store(retained.object_id)
    async with transaction(store.runtime.storage) as session:
        rows = list(await session.scalars(select(ThreadObjectRow)))
        for row in rows:
            row.accessed_at = await now(session) - timedelta(hours=23)
    assert await store.read(refreshed.object_id) == b"read"
    async with transaction(store.runtime.storage) as session:
        rows = list(await session.scalars(select(ThreadObjectRow).where(ThreadObjectRow.id != refreshed.object_id)))
        for row in rows:
            row.accessed_at = await now(session) - timedelta(days=2)
    with pytest.raises(ObjectStoreError):
        await store.info(stale.object_id)
    with pytest.raises(ObjectStoreError):
        await store.store(stale.object_id)
    assert {item.object_id for item in await store.list()} == {retained.object_id, refreshed.object_id}
    await clean(store.runtime)
    async with transaction(store.runtime.storage) as session:
        assert await session.get(ThreadObjectRow, stale.object_id) is None
    assert await store.read(retained.object_id) == b"keep"
    assert await store.read(refreshed.object_id) == b"read"


async def test_cleanup_failure_keeps_retryable_owner_without_failing_import(
    service, scripted_model, runs_kit, monkeypatch
):
    store = await setup_store(service, scripted_model, runs_kit, count=1)
    first = await store.put(b"one", media_type="image/png")
    backend_delete = store.runtime.objects.delete

    async def unavailable(key):
        raise OSError("backend unavailable")

    monkeypatch.setattr(store.runtime.objects, "delete", unavailable)
    second = await store.put(b"two", media_type="image/png")
    assert await store.read(second.object_id) == b"two"
    async with transaction(store.runtime.storage) as session:
        assert (await session.get(ThreadObjectRow, first.object_id)).retention == "deleted"
    monkeypatch.setattr(store.runtime.objects, "delete", backend_delete)
    await clean(store.runtime)
    async with transaction(store.runtime.storage) as session:
        assert await session.get(ThreadObjectRow, first.object_id) is None


async def test_production_host_selects_capability_and_binds_private_store(executing, scripted_model, runs_kit):
    agent = await runs_kit.create_agent(executing, scripted_model, toolsets={"object_store": {"enabled": True}})
    scripted_model.call("asset_list", {}, call_id="private-list")
    scripted_model.say("listed")
    submitted = await runs_kit.start_thread(executing, agent, "List private objects")
    completed = await runs_kit.sealed(executing, submitted["run"]["id"])
    assert completed["status"] == "completed", completed
    assert completed["output"] == "listed"


@pytest.mark.parametrize("server_default,override", [(True, None), (False, True), (True, False), (False, None)])
async def test_mcp_object_refs_deliver_exact_private_bytes(service, scripted_model, runs_kit, server_default, override):
    import base64
    import json

    import httpx2
    from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
    from a13n_harness.capabilities import ObjectStoreCapability
    from a13n_service.providers.tools.mcp import mcp_capability, mcp_client
    from fastmcp import FastMCP
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    from .test_connections import serve

    store = await setup_store(service, scripted_model, runs_kit, size=100)
    item = await store.put(b"\x00real MCP payload\xff", media_type="image/png")
    server, received, checked = FastMCP("Exact private transport"), [], []

    @server.tool
    def send(data_base64: str) -> str:
        received.append(base64.b64decode(data_base64))
        return "sent"

    step = 0

    async def model(messages, info):
        nonlocal step
        step += 1
        if step == 1:
            yield {0: DeltaToolCall(name="send", json_args=json.dumps({"data_base64": item.ref}), tool_call_id="send")}
        else:
            yield "done"

    async with serve(server.http_app(path="/mcp")) as url:
        http = httpx2.AsyncClient()
        async with mcp_client(url + "/mcp", "connection-media", http, timeout=5) as client:

            async def acquire():
                return client

            async def prepare():
                return acquire

            async def check(dispatch):
                checked.append(dispatch)

            capability = mcp_capability(
                "connection-media",
                prepare,
                tools=None,
                check=check,
                supports_object_refs=server_default,
                object_ref_tools={} if override is None else {"send": override},
            )
            executable = HarnessBuilder().build(
                AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=model),
                capabilities=[ObjectStoreCapability(), capability],
            )
            result = await executable.run("send it", bindings=RunBindings.embedded(object_store=store))
    enabled = server_default if override is None else override
    assert result.output_or_raise() == "done"
    assert received == ([b"\x00real MCP payload\xff"] if enabled else [])
    assert len(checked) == (1 if enabled else 0)
    async with transaction(store.runtime.storage) as session:
        assert await session.scalar(select(func.count()).select_from(AssetRow)) == 0


async def test_late_blob_write_after_pending_cleanup_is_reconciled(service, scripted_model, runs_kit, monkeypatch):
    import asyncio

    import a13n_service.runs.object_store as library

    store = await setup_store(service, scripted_model, runs_kit)
    entered, release = asyncio.Event(), asyncio.Event()
    backend_put = store.runtime.objects.put
    observed = []

    async def delayed_put(key, data, *, content_type):
        observed.append(key)
        entered.set()
        await release.wait()
        return await backend_put(key, data, content_type=content_type)

    monkeypatch.setattr(store.runtime.objects, "put", delayed_put)
    writer = asyncio.create_task(store.put(b"late", media_type="image/png"))
    await entered.wait()
    original_now = library.now

    async def later(session):
        return await original_now(session) + timedelta(hours=2)

    monkeypatch.setattr(library, "now", later)
    cleaner = library.ThreadObjectCleaner(store.runtime)
    try:
        await cleaner()
        async with transaction(store.runtime.storage) as session:
            assert not list(await session.scalars(select(ThreadObjectRow)))
    finally:
        monkeypatch.setattr(library, "now", original_now)
        release.set()
    with pytest.raises(ObjectStoreError):
        await writer
    key = observed[0]
    assert await store.runtime.objects.get(key) == b"late"
    await cleaner()
    assert await store.runtime.objects.get(key) is None


async def test_object_authority_takes_thread_lock_before_run_lock(service, scripted_model, runs_kit, monkeypatch):
    import asyncio

    import a13n_service.runs.object_store as library
    from a13n_service.runs.tables import ThreadRow
    from anyio import move_on_after

    store = await setup_store(service, scripted_model, runs_kit)
    item = await store.put(b"one", media_type="image/png")
    owner_entered = asyncio.Event()
    original_owner = library.lock_owner

    async def owner(*args, **kwargs):
        owner_entered.set()
        return await original_owner(*args, **kwargs)

    monkeypatch.setattr(library, "lock_owner", owner)
    async with transaction(store.runtime.storage) as session:
        await session.scalar(select(ThreadRow).where(ThreadRow.id == store.lease.thread_id).with_for_update())
        task = asyncio.create_task(store.info(item.object_id))
        with move_on_after(0.1):
            await owner_entered.wait()
        assert not owner_entered.is_set(), "Store took Run authority while blocked behind the Thread lock"
    assert (await task).object_id == item.object_id
    assert owner_entered.is_set()


async def test_mcp_screenshot_can_be_forwarded_without_model_base64(service, scripted_model, runs_kit):
    import base64
    import json
    import re
    from io import BytesIO

    import httpx2
    from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
    from a13n_harness.capabilities import ObjectStoreCapability
    from a13n_service.providers.tools.mcp import mcp_capability, mcp_client
    from fastmcp import FastMCP
    from fastmcp.utilities.types import Image
    from PIL import Image as PillowImage
    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    from .test_connections import serve

    image = BytesIO()
    PillowImage.new("RGB", (8, 8), "red").save(image, format="PNG")
    png = image.getvalue()
    store = await setup_store(service, scripted_model, runs_kit, size=1024)
    server, received = FastMCP("Screenshot bridge"), []

    @server.tool
    def screenshot():
        return Image(data=png, format="png")

    @server.tool
    def send_image(data_base64: str) -> str:
        received.append(base64.b64decode(data_base64))
        return "sent"

    step = 0

    async def model(messages, info):
        nonlocal step
        step += 1
        if step == 1:
            yield {0: DeltaToolCall(name="screenshot", json_args="{}", tool_call_id="capture")}
        elif step == 2:
            parts = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
            assert len(parts[0].files) == 1, repr(parts[0].content)
            ref = re.search(r"a13n-object://obj_[a-zA-Z0-9_-]+", str(parts[0].content))
            assert ref is not None
            yield {
                0: DeltaToolCall(name="send_image", json_args=json.dumps({"data_base64": ref[0]}), tool_call_id="send")
            }
        else:
            yield "done"

    async with serve(server.http_app(path="/mcp")) as url:
        async with mcp_client(url + "/mcp", "connection-media", httpx2.AsyncClient(), timeout=5) as client:

            async def acquire():
                return client

            async def prepare():
                return acquire

            async def check(dispatch):
                pass

            capability = mcp_capability(
                "connection-media", prepare, tools=None, check=check, object_ref_tools={"send_image": True}
            )
            executable = HarnessBuilder().build(
                AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=model),
                capabilities=[ObjectStoreCapability(), capability],
            )
            result = await executable.run("capture and send", bindings=RunBindings.embedded(object_store=store))
    assert result.output_or_raise() == "done", result
    assert received == [png]
    calls = [part for message in result.all_messages() for part in message.parts if part.part_kind == "tool-call"]
    assert calls[1].args_as_dict()["data_base64"].startswith("a13n-object://obj_")


async def test_orphan_reconciliation_pages_past_live_objects(service, scripted_model, runs_kit):
    from a13n_service.runs.object_store import ThreadObjectCleaner

    store = await setup_store(service, scripted_model, runs_kit)
    first = await store.put(b"one", media_type="image/png")
    second = await store.put(b"two", media_type="image/png")
    orphan = "thread-objects/zz/zz/obj_orphan"
    await store.runtime.objects.put(orphan, b"unowned", content_type="image/png")
    settings = store.runtime.settings.model_copy(
        update={"control": store.runtime.settings.control.model_copy(update={"sweep_batch": 1})}
    )
    cleaner = ThreadObjectCleaner(replace(store.runtime, settings=settings))
    for _ in range(4):
        await cleaner()
    assert cleaner.after is None
    assert await store.runtime.objects.get(orphan) is None
    assert await store.read(first.object_id) == b"one"
    assert await store.read(second.object_id) == b"two"


@pytest.mark.parametrize("operation", ["evict", "delete", "backend_failure", "authority_lost"])
async def test_inflight_read_distinguishes_catalog_removal_from_backend_failure(
    service, scripted_model, runs_kit, monkeypatch, operation
):
    import asyncio

    from a13n_service.infra.errors import ServiceError

    store = await setup_store(service, scripted_model, runs_kit, count=1)
    first = await store.put(b"one", media_type="image/png")
    entered, release = asyncio.Event(), asyncio.Event()
    backend_get = store.runtime.objects.get
    backend_error = ServiceError("unavailable", "backend failure", {"dependency": "objects"})

    async def delayed_get(key):
        entered.set()
        await release.wait()
        if operation in {"backend_failure", "authority_lost"}:
            raise backend_error
        return await backend_get(key)

    monkeypatch.setattr(store.runtime.objects, "get", delayed_get)
    reader = asyncio.create_task(store.read(first.object_id))
    await entered.wait()
    try:
        if operation == "evict":
            await store.put(b"two", media_type="image/png")
        elif operation == "delete":
            await store.delete(first.object_id)
        elif operation == "authority_lost":

            async def lost(session):
                raise LeaseLost()

            monkeypatch.setattr(store, "_authorize", lost)
    finally:
        release.set()
    expected = (
        ObjectStoreError
        if operation in {"evict", "delete"}
        else (LeaseLost if operation == "authority_lost" else ServiceError)
    )
    with pytest.raises(expected) as error:
        await reader
    if operation in {"evict", "delete"}:
        assert error.value.code == "object_ref_not_found"
    elif operation == "backend_failure":
        assert error.value is backend_error
