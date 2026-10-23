"""Persistent adapters use real Harness execution, PostgreSQL, object storage and mailbox delivery."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from a13n_harness.codeact.config import CodeActConfig
from a13n_harness.codeact.state import CodeActStoredValues
from a13n_harness.tool_execution import (
    StoredToolExecution,
    ToolExecutionAdmissionRejected,
    ToolExecutionArtifact,
    ToolExecutionRecord,
    ToolExecutionRuntime,
)
from a13n_service.infra import outbox
from a13n_service.infra.db import transaction
from a13n_service.runs.attempts import AttemptControl, LeaseLost
from a13n_service.runs.claim import claim
from a13n_service.runs.execute import execute
from a13n_service.runs.tables import InboxEntryRow, ToolExecutionRow
from a13n_service.runs.tool_executions.cleanup import prune, reconcile
from a13n_service.runs.tool_executions.delivery import tool_results
from a13n_service.runs.tool_executions.payload import Payload
from a13n_service.runs.tool_executions.state import SharedCodeActState
from a13n_service.runs.tool_executions.store import ORIGIN, Store
from pydantic_ai import BinaryContent, ToolReturn
from sqlalchemy import func, select, update

pytestmark = pytest.mark.anyio


async def _origin(service, scripted_model, runs_kit):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    started = await runs_kit.start_thread(service, agent, "work")
    (lease,) = await claim(service.runtime, worker_id="worker-a", worker_build="test", limit=1)
    return started, lease


def _record(lease, name="exec-original"):
    return ToolExecutionRecord(
        execution_id=name,
        thread_id=lease.thread_id,
        run_id="harness-original",
        tool_call_id="call-original",
        tool_name="slow_tool",
    )


async def _save(store, lease, value):
    token = ORIGIN.set(lease)
    try:
        await store.save(value)
    finally:
        ORIGIN.reset(token)


async def _deliver(runtime):
    claims = await outbox.claim(
        runtime.storage, "tool_result", owner="control-test", limit=10, lease_seconds=60, max_attempts=12
    )
    for item in claims:
        await tool_results(runtime)(item)
    return claims


async def test_original_invocation_survives_run_and_another_runtime_reads_result(service, scripted_model, runs_kit):
    _, lease = await _origin(service, scripted_model, runs_kit)
    first = ToolExecutionRuntime(store=Store(service.runtime))
    second = ToolExecutionRuntime(store=Store(service.runtime))
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        entered.set()
        await finish.wait()
        return {"answer": 42}

    token = ORIGIN.set(lease)
    try:
        record = first.admit(
            operation,
            thread_id=lease.thread_id,
            run_id="harness-original",
            tool_call_id="call-original",
            tool_name="slow_tool",
        )
    finally:
        ORIGIN.reset(token)
    try:
        await asyncio.wait_for(entered.wait(), 5)
        first.release_foreground(lease.thread_id, record.execution_id)
        scripted_model.say("The original Run can finish")
        await execute(service.runtime, lease, AttemptControl())
        assert (await runs_kit.get_run(service, lease.run_id))["status"] == "completed"
        assert (await second.info(lease.thread_id, record.execution_id)).status == "running"
        finish.set()
        await first.join()
        result = await second.result(lease.thread_id, record.execution_id)
        assert result.result == {"answer": 42}
        assert result.record.foreground_released
        assert calls == 1
        assert await second.result("another-thread", record.execution_id) is None
        assert len(await _deliver(service.runtime)) == 1
        entries = await runs_kit.inbox(service, lease.thread_id)
        assert entries[-1]["kind"] == "tool_result"
        assert entries[-1]["payload"]["execution_id"] == record.execution_id
        assert (await second.result(lease.thread_id, record.execution_id)).record.delivery_acknowledged
        # Delivery admits an ordinary successor. No worker affinity is introduced.
        (successor,) = await claim(service.runtime, worker_id="worker-b", worker_build="test", limit=1)
        assert successor.thread_id == lease.thread_id
        scripted_model.say("The result is available")
        await execute(service.runtime, successor, AttemptControl())
        assert await _deliver(service.runtime) == []
        assert (await second.result(lease.thread_id, record.execution_id)).result == {"answer": 42}
    finally:
        finish.set()
        await first.close()
        await second.close()


@pytest.mark.parametrize("acknowledge", [False, True])
async def test_retention_expires_reads_objects_and_pending_delivery(service, scripted_model, runs_kit, acknowledge):
    _, lease = await _origin(service, scripted_model, runs_kit)
    store, record = Store(service.runtime), _record(lease)
    await _save(store, lease, StoredToolExecution(record))
    complete = record.model_copy(
        update={
            "status": "succeeded",
            "completed_at": datetime.now(UTC),
            "result_available": True,
            "foreground_released": True,
        }
    )
    value = StoredToolExecution(
        complete, {"file": "old/path"}, (ToolExecutionArtifact("old/path", b"\xffbytes", ".bin"),)
    )
    await _save(store, lease, value)
    if acknowledge:
        await store.acknowledge_delivery(lease.thread_id, record.execution_id)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(ToolExecutionRow, record.execution_id)
        deadline, delivered_at = row.expires_at, row.first_delivered_at
    assert (await store.get(lease.thread_id, record.execution_id)).artifacts == value.artifacts
    await _save(store, lease, value)
    if acknowledge:
        await store.acknowledge_delivery(lease.thread_id, record.execution_id)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(ToolExecutionRow, record.execution_id)
        assert (row.expires_at, row.first_delivered_at) == (deadline, delivered_at)
        assert deadline <= complete.completed_at + timedelta(
            seconds=service.runtime.settings.tool_executions.max_retention_seconds
        )
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await store.get(lease.thread_id, record.execution_id) is None
    assert await store.list(lease.thread_id, offset=0, limit=10) == ()
    assert await prune(service.runtime) == 1
    assert (
        await service.runtime.objects.keys(f"tool-executions/{lease.workspace_id}/{record.execution_id}", limit=10)
        == []
    )
    assert await _deliver(service.runtime) == []


async def test_lost_owner_is_terminal_and_late_publication_cannot_replace_it(service, scripted_model, runs_kit):
    _, lease = await _origin(service, scripted_model, runs_kit)
    store, record = Store(service.runtime), _record(lease)
    await _save(store, lease, StoredToolExecution(record))
    async with transaction(service.runtime.storage) as session:
        await session.execute(update(ToolExecutionRow).values(lease_expires_at=func.now() - timedelta(seconds=1)))
    assert await reconcile(service.runtime) == 1
    assert await reconcile(service.runtime) == 0
    late = record.model_copy(
        update={"status": "succeeded", "completed_at": datetime.now(UTC), "result_available": True}
    )
    await _save(store, lease, StoredToolExecution(late, "too late"))
    result = await store.get(lease.thread_id, record.execution_id)
    assert result.record.status == "lost"
    assert result.result is None
    assert len(await _deliver(service.runtime)) == 1


async def test_publication_retry_commits_result_and_notification_once(service, scripted_model, runs_kit, monkeypatch):
    _, lease = await _origin(service, scripted_model, runs_kit)
    store, record = Store(service.runtime), _record(lease)
    await _save(store, lease, StoredToolExecution(record))
    complete = record.model_copy(
        update={
            "status": "succeeded",
            "completed_at": datetime.now(UTC),
            "result_available": True,
            "foreground_released": True,
        }
    )
    save, attempts = store._save, 0

    async def uncertain(value):
        nonlocal attempts
        attempts += 1
        await save(value)
        if attempts == 1:
            raise ConnectionError("Commit response lost")

    monkeypatch.setattr(store, "_save", uncertain)
    await _save(store, lease, StoredToolExecution(complete, 42))
    assert attempts == 2
    assert (await store.get(lease.thread_id, record.execution_id)).result == 42
    assert len(await _deliver(service.runtime)) == 1
    async with transaction(service.runtime.storage) as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(InboxEntryRow).where(InboxEntryRow.kind == "tool_result")
            )
            == 1
        )


async def test_admission_requires_current_attempt_and_matching_thread(service, scripted_model, runs_kit):
    _, lease = await _origin(service, scripted_model, runs_kit)
    store, record = Store(service.runtime), _record(lease)
    with pytest.raises(ToolExecutionAdmissionRejected):
        await _save(store, replace(lease, token="stale"), StoredToolExecution(record))
    with pytest.raises(LeaseLost):
        await _save(store, lease, StoredToolExecution(record.model_copy(update={"thread_id": "other"})))
    assert await store.get(lease.thread_id, record.execution_id) is None


async def test_admission_waits_for_thread_before_locking_run(service, scripted_model, runs_kit, monkeypatch):
    from a13n_service.infra.db import lock, short_session
    from a13n_service.runs import attempts
    from a13n_service.runs.tables import RunRow, ThreadRow
    from sqlalchemy import text

    _, lease = await _origin(service, scripted_model, runs_kit)
    store, record = Store(service.runtime), _record(lease)
    entered = asyncio.Event()
    admission_pid = None
    native_lock = attempts.lock

    async def observe_lock(session, model, key, **kwargs):
        nonlocal admission_pid
        if admission_pid is None:
            admission_pid = await session.scalar(text("SELECT pg_backend_pid()"))
            entered.set()
        return await native_lock(session, model, key, **kwargs)

    monkeypatch.setattr(attempts, "lock", observe_lock)
    async with asyncio.timeout(5), asyncio.TaskGroup() as tasks:
        async with transaction(service.runtime.storage) as session:
            await lock(session, ThreadRow, lease.thread_id)
            holder_pid = await session.scalar(text("SELECT pg_backend_pid()"))
            admission = tasks.create_task(_save(store, lease, StoredToolExecution(record)))
            await entered.wait()
            # Wait for real database contention, then exercise the poller's Thread → Run suffix.
            while True:
                async with short_session(service.runtime.storage) as observation:
                    blocked = await observation.scalar(
                        text("SELECT :holder = ANY(pg_blocking_pids(:admission))"),
                        {"holder": holder_pid, "admission": admission_pid},
                    )
                if blocked:
                    break
                await asyncio.sleep(0)
            assert await lock(session, RunRow, lease.run_id, skip_locked=True) is not None
        await admission
    assert await store.get(lease.thread_id, record.execution_id) is not None


async def test_shared_codeact_late_writes_and_fresh_snapshot_do_not_overwrite_each_other(
    service, scripted_model, runs_kit
):
    _, lease = await _origin(service, scripted_model, runs_kit)
    a, b = (SharedCodeActState(service.runtime.storage, lease.thread_id) for _ in range(2))
    seed = CodeActStoredValues(values={"seed": "original"})
    await a.initialize(lease.thread_id, seed)
    await a.store("late", 42, CodeActConfig())
    await b.initialize(lease.thread_id, seed)
    await asyncio.gather(a.store("a", 1, CodeActConfig()), b.store("b", 2, CodeActConfig()))
    assert (await b.snapshot()).values == {"seed": "original", "late": 42, "a": 1, "b": 2}
    await a.forget("seed")
    assert not await b.forget("seed")
    assert "seed" not in (await b.snapshot()).values


def test_native_payload_preserves_media_order_and_spill_bytes():
    record = _record(SimpleNamespace(thread_id="thread"))
    image = BinaryContent(data=b"\x89PNG\xff", media_type="image/png")
    result = ToolReturn(return_value={"z": 1, "a": 2}, content=["before", image, "after"], metadata={"key": "value"})
    stored = StoredToolExecution(record, result, (ToolExecutionArtifact("old/path", b"\xffbinary", ".bin"),))
    loaded = Payload.model_validate_json(Payload.encode(stored)).execution(record)
    assert isinstance(loaded.result, ToolReturn)
    assert list(loaded.result.return_value) == ["z", "a"]
    assert loaded.result.content[0] == "before"
    assert loaded.result.content[1].data == image.data
    assert loaded.result.content[2] == "after"
    assert loaded.artifacts == stored.artifacts


@pytest.mark.parametrize("handoff", ["timeout", "steer"])
@pytest.mark.parametrize("continuation", ["automatic", "inline"])
async def test_service_retains_real_mcp_call_after_run_seals(
    service, scripted_model, runs_kit, handoff, continuation, monkeypatch
):
    import a13n_service.resources.connections.runtime as connections
    from fastmcp import FastMCP

    from .test_connections import create, serve

    await runs_kit.pause_sweeps(service)
    server = FastMCP("Retained call")
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = 0
    clients = []
    original = connections.mcp_client

    def capture(*args, **kwargs):
        client = original(*args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(connections, "mcp_client", capture)

    @server.tool
    async def slow() -> str:
        nonlocal calls
        calls += 1
        entered.set()
        await finish.wait()
        return "retained answer"

    policy = service.runtime.settings.tool_executions.model_copy(
        update={"foreground_wait_seconds": 0.05 if handoff == "timeout" else 60}
    )
    runtime = replace(service.runtime, settings=service.runtime.settings.model_copy(update={"tool_executions": policy}))
    async with serve(server.http_app(path="/mcp")) as url:
        connection = await create(service, {"config": {"url": url + "/mcp"}})
        agent = await runs_kit.create_agent(
            service,
            scripted_model,
            connection_tools=[
                {"connection_id": connection["id"], "permission": "allow"},
            ],
        )
        started = await runs_kit.start_thread(
            service,
            agent,
            "start slow work",
            options={
                "execution": {
                    "tools": {
                        "mode": "auto" if handoff == "timeout" else "inline",
                        "foreground_wait_seconds": policy.foreground_wait_seconds,
                    },
                    "steering": "immediate",
                }
            },
        )
        scripted_model.call("slow", {}, call_id="call-retained")
        scripted_model.say("Working in the background")
        (lease,) = await claim(runtime, worker_id="worker-a", worker_build="test", limit=1)
        running = asyncio.create_task(execute(runtime, lease, AttemptControl()))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            if handoff == "steer":
                response = await runs_kit.submit(service, lease.thread_id, runs_kit.message(agent, "continue now"))
                assert response.status_code == 201, response.text
            await asyncio.wait_for(running, 10)
            assert (await runs_kit.get_run(service, started["run"]["id"]))["status"] == "completed"
            assert clients and clients[0].is_connected()
            (record,) = await runtime.executions.summarize(lease.thread_id)
            assert record.status == "running" and record.foreground_released
            assert calls == 1
            if handoff == "steer":
                assert any(
                    entry["status"] == "consumed" and "continue now" in str(entry["payload"])
                    for entry in await runs_kit.inbox(service, lease.thread_id)
                )
            finish.set()
            await asyncio.wait_for(runtime.executions.join(), 10)
            assert not clients[0].is_connected()
            # A different worker has only shared state, yet retrieves the original native MCP return.
            second = replace(runtime)
            result = await second.executions.result(lease.thread_id, record.execution_id)
            assert result.record.status == "succeeded"
            assert "retained answer" in str(result.result)
            thread = await runs_kit.get_thread(service, lease.thread_id)
            changed = await service.client.patch(
                f"{service.api}/threads/{lease.thread_id}",
                json={"execution": {"tools": {"mode": "inline"}, "steering": "boundary"}},
                headers=runs_kit.if_match(thread),
            )
            assert changed.status_code == 200, changed.text
            if continuation == "inline":
                submitted = await runs_kit.submit(
                    service, lease.thread_id, runs_kit.message(agent, "read previous result")
                )
                assert submitted.status_code == 201, submitted.text
            assert len(await _deliver(runtime)) == 1
            (successor,) = await claim(second, worker_id="worker-b", worker_build="test", limit=1)
            accepted = await runs_kit.get_run(service, successor.run_id)
            if continuation == "automatic":
                assert accepted["options"]["execution"] == started["run"]["options"]["execution"]
            else:
                assert accepted["options"]["execution"]["tools"]["mode"] == "inline"
                assert accepted["options"]["execution"]["steering"] == "boundary"
            scripted_model.call("tool_execution_result", {"execution_id": record.execution_id}, call_id="read-result")
            scripted_model.say("Read the retained result")
            await execute(second, successor, AttemptControl())
            assert (await runs_kit.get_run(service, successor.run_id))["status"] == "completed"
            assert calls == 1
        finally:
            finish.set()
            await runtime.executions.close()
            if not running.done():
                running.cancel()
            await asyncio.gather(running, return_exceptions=True)


@pytest.mark.parametrize("revoke", [False, True])
async def test_whole_codeact_continues_nested_dispatch_after_origin_seals(
    service, scripted_model, runs_kit, monkeypatch, listen, revoke, caplog
):
    from a13n_harness.capabilities import CodeActCapability
    from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
    from a13n_service.api_tools import tool_name
    from a13n_service.runs.host import Host
    from fastmcp import FastMCP
    from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering

    from .test_connections import create, serve
    from .test_platform_tools import set_role
    from .test_runs_memory import mount, team_memory

    # A distribution can contribute CodeAct through its Harness capabilities. Exercise the actual runner,
    # not a task that simulates its nested dispatch or state owner.
    capabilities = Host.capabilities
    read_name = tool_name(service.app.openapi()["paths"]["/api/v1/agents"]["get"]["operationId"])

    class ProgramTools(AbstractCapability):
        def get_ordering(self):
            return CapabilityOrdering(wrapped_by=(CodeActCapability,))

        def get_wrapper_toolset(self, toolset):
            return CodeActPolicyToolset(
                wrapped=toolset,
                policy=CodeActToolPolicy(
                    tools={
                        "slow": True,
                        "memory_file_create": True,
                        read_name: True,
                        "store": True,
                        "load": True,
                        "forget": True,
                    }
                ),
            )

    def with_codeact(self, agent, *, retain_tools=False):
        return capabilities(self, agent, retain_tools=retain_tools) + (
            [ProgramTools(), CodeActCapability()] if retain_tools else []
        )

    monkeypatch.setattr(Host, "capabilities", with_codeact)
    await runs_kit.pause_sweeps(service)
    memory = await team_memory(service)
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = 0
    server = FastMCP("CodeAct gate")

    @server.tool
    async def slow() -> str:
        nonlocal calls
        calls += 1
        entered.set()
        await finish.wait()
        return "original invocation"

    async with serve(server.http_app(path="/mcp")) as url, listen(service.app) as control_url:
        connection = await create(service, {"config": {"url": url + "/mcp"}})
        agent = await runs_kit.create_agent(
            service,
            scripted_model,
            connection_tools=[{"connection_id": connection["id"], "permission": "allow"}],
            toolsets={"memory": {"enabled": True}, "agents": {"enabled": True}},
        )
        started = await runs_kit.start_thread(
            service,
            agent,
            "run the program",
            memories=[mount(memory)],
            options={"execution": {"tools": {"foreground_wait_seconds": 0.05}}},
        )
        code = (
            "first = await slow()\n"
            "saved = await memory_file_create(memory='team', path='late.md', content='After the Run')\n"
            f"agents = await {read_name}()\n"
            "await store(key='late', value={'first': first, 'saved': saved, 'agents': agents})\n"
            "'program completed'"
        )
        scripted_model.call("run_code", {"code": code}, call_id="whole-program")
        scripted_model.say("Program retained")
        settings = service.runtime.settings
        runtime = replace(
            service.runtime,
            settings=settings.model_copy(
                update={
                    "server": settings.server.model_copy(update={"control_url": control_url}),
                    "tool_executions": settings.tool_executions.model_copy(update={"foreground_wait_seconds": 0.05}),
                }
            ),
        )
        (lease,) = await claim(runtime, worker_id="worker-codeact", worker_build="test", limit=1)
        running = asyncio.create_task(execute(runtime, lease, AttemptControl()))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            await asyncio.wait_for(running, 10)
            observed = await runs_kit.get_run(service, started["run"]["id"])
            assert observed["status"] == "completed", (
                observed,
                [entry.__dict__.get("exception_details") for entry in caplog.records if entry.levelno >= 40],
            )
            (record,) = await runtime.executions.summarize(lease.thread_id)
            assert record.tool_name == "run_code" and record.status == "running"
            if revoke:
                async with transaction(runtime.storage) as session:
                    await set_role(session, service, "viewer")
            finish.set()
            await asyncio.wait_for(runtime.executions.join(), 10)
            result = await runtime.executions.result(lease.thread_id, record.execution_id)
            snapshot = await SharedCodeActState(runtime.storage, lease.thread_id).snapshot()
            assert calls == 1
            if revoke:
                assert "late" not in snapshot.values
            else:
                assert "program completed" in str(result.result), result
                assert snapshot.values["late"]["first"]
                assert agent["id"] in str(snapshot.values["late"]["agents"])
                saved = await service.client.get(f"{service.api}/memories/{memory['id']}/files/late.md")
                assert saved.status_code == 200 and saved.json()["content"] == "After the Run"
        finally:
            finish.set()
            await runtime.executions.close()
            if not running.done():
                running.cancel()
            await asyncio.gather(running, return_exceptions=True)


@pytest.mark.parametrize("status", ["queued", "running", "succeeded", "lost", "expired"])
async def test_recovery_preserves_admission_and_other_pending_calls(service, scripted_model, runs_kit, status):
    from a13n_harness import HarnessState
    from a13n_harness.recovery import prepare_tool_recovery
    from a13n_service.runs.tool_executions.recovery import restore
    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

    _, lease = await _origin(service, scripted_model, runs_kit)
    store, record = Store(service.runtime), _record(lease)
    await _save(store, lease, StoredToolExecution(record))
    if status != "queued":
        terminal = status != "running"
        record = record.model_copy(
            update={
                "status": "succeeded" if status == "expired" else status,
                "completed_at": datetime.now(UTC) if terminal else None,
                "result_available": status in {"succeeded", "expired"},
                "foreground_released": terminal,
            }
        )
        await _save(store, lease, StoredToolExecution(record, "retained" if record.result_available else None))
    if status == "expired":
        async with transaction(service.runtime.storage) as session:
            row = await session.get_one(ToolExecutionRow, record.execution_id)
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await prune(service.runtime)
        assert await store.get(lease.thread_id, record.execution_id) is None
        async with transaction(service.runtime.storage) as session:
            assert await session.get(ToolExecutionRow, record.execution_id) is not None
    state = HarnessState.new(
        thread_id=lease.thread_id,
        message_history=[
            ModelResponse(
                parts=[
                    ToolCallPart("slow_tool", {}, "call-original"),
                    ToolCallPart("unadmitted", {}, "call-new"),
                    ToolCallPart("answered", {}, "call-done"),
                ]
            ),
            ModelRequest(parts=[ToolReturnPart("answered", "already delivered", "call-done")]),
        ],
    )
    claims = []
    if status in {"succeeded", "lost"}:
        claims = await outbox.claim(
            service.runtime.storage,
            "tool_result",
            owner="existing-delivery",
            limit=1,
            lease_seconds=60,
            max_attempts=12,
        )
        assert len(claims) == 1
    assert await restore(service.runtime, state, thread_id="other-thread", origin_run_id=lease.run_id) == state
    assert await restore(service.runtime, state, thread_id=lease.thread_id, origin_run_id="other-run") == state
    restored = await restore(service.runtime, state, thread_id=lease.thread_id, origin_run_id=lease.run_id)
    plan = prepare_tool_recovery(restored.message_history, "always")
    assert set(plan.pending) == {"call-new"}
    returns = [
        part for message in restored.message_history for part in message.parts if isinstance(part, ToolReturnPart)
    ]
    assert len(returns) == 2
    assert returns[-1].content["execution_id"] == record.execution_id
    assert (
        "expired" in returns[-1].content["message"]
        if status == "expired"
        else "runtime" in returns[-1].content["message"]
    )
    assert await restore(service.runtime, restored, thread_id=lease.thread_id, origin_run_id=lease.run_id) == restored
    if not record.terminal:
        completed = record.model_copy(
            update={"status": "succeeded", "completed_at": datetime.now(UTC), "result_available": True}
        )
        await _save(store, lease, StoredToolExecution(completed, "late"))
        assert (await store.get(lease.thread_id, record.execution_id)).record.foreground_released
    if status == "expired":
        scripted_model.say("Completed")
        await execute(service.runtime, lease, AttemptControl())
        await prune(service.runtime)
        async with transaction(service.runtime.storage) as session:
            assert await session.get(ToolExecutionRow, record.execution_id) is None
    elif claims:
        # Restoration must preserve the active claim rather than reset or duplicate it.
        assert await _deliver(service.runtime) == []
        await tool_results(service.runtime)(claims[0])
        assert (await store.get(lease.thread_id, record.execution_id)).record.delivery_acknowledged
    else:
        assert len(await _deliver(service.runtime)) == 1


async def test_takeover_restores_original_execution_without_repeating_effects(
    service, scripted_model, runs_kit, monkeypatch
):
    from a13n_service.runs.seal import LeaseExpirer
    from a13n_service.runs.tables import AttemptRow, RunRow
    from fastmcp import FastMCP

    from .test_connections import create, serve

    await runs_kit.pause_sweeps(service)
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = 0
    server = FastMCP("Retained takeover")

    @server.tool
    async def slow() -> str:
        nonlocal calls
        calls += 1
        entered.set()
        await finish.wait()
        return "executed once"

    async with serve(server.http_app(path="/mcp")) as url:
        connection = await create(service, {"config": {"url": url + "/mcp"}})
        agent = await runs_kit.create_agent(
            service,
            scripted_model,
            connection_tools=[
                {"connection_id": connection["id"], "permission": "allow"},
            ],
        )
        await runs_kit.start_thread(service, agent, "perform once")
        scripted_model.call("slow", {}, call_id="retained-original")
        (lease,) = await claim(service.runtime, worker_id="worker-a", worker_build="test", limit=1)
        running = asyncio.create_task(execute(service.runtime, lease, AttemptControl()))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            (original,) = await service.runtime.executions.summarize(lease.thread_id)
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            async with transaction(service.runtime.storage) as session:
                await session.execute(
                    update(AttemptRow)
                    .where(AttemptRow.id == lease.attempt_id)
                    .values(
                        lease_expires_at=AttemptRow.created_at,
                    )
                )
            await LeaseExpirer(service.runtime, batch=10)()
            async with transaction(service.runtime.storage) as session:
                await session.execute(
                    update(RunRow).where(RunRow.id == lease.run_id).values(available_at=RunRow.created_at)
                )
            second = replace(service.runtime)
            (successor,) = await claim(second, worker_id="worker-b", worker_build="test", limit=1)
            scripted_model.say("Original execution retained")
            await execute(second, successor, AttemptControl())
            result = await runs_kit.get_run(service, lease.run_id)
            assert result["status"] == "completed" and result["attempts"] == 2, result
            await scripted_model.request()
            resumed = await scripted_model.request()
            returned = [message for message in resumed["messages"] if message["role"] == "tool"]
            assert any(original.execution_id in str(message["content"]) for message in returned), returned
            finish.set()
            await asyncio.wait_for(service.runtime.executions.join(), 10)
            assert calls == 1
            assert (await second.executions.result(lease.thread_id, original.execution_id)).record.status == "succeeded"
        finally:
            finish.set()
            await service.runtime.executions.close()
            if not running.done():
                running.cancel()
            await asyncio.gather(running, return_exceptions=True)


@pytest.mark.parametrize("pruned", [False, True])
async def test_owner_loss_during_upload_does_not_leave_orphan_payload(
    service, scripted_model, runs_kit, monkeypatch, pruned
):

    _, lease = await _origin(service, scripted_model, runs_kit)
    store, record = Store(service.runtime), _record(lease)
    await _save(store, lease, StoredToolExecution(record))
    put = service.runtime.objects.put

    async def lose_during_upload(key, data, *, content_type):
        async with transaction(service.runtime.storage) as session:
            row = await session.get_one(ToolExecutionRow, record.execution_id)
            row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        assert await reconcile(service.runtime) == 1
        if pruned:
            scripted_model.say("Completed")
            await execute(service.runtime, lease, AttemptControl())
            async with transaction(service.runtime.storage) as session:
                row = await session.get_one(ToolExecutionRow, record.execution_id)
                row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await prune(service.runtime)
        return await put(key, data, content_type=content_type)

    # Only intercept the execution namespace; checkpoint writes retain their ordinary transport.
    async def intercept(key, data, *, content_type):
        if key.startswith("tool-executions/"):
            return await lose_during_upload(key, data, content_type=content_type)
        return await put(key, data, content_type=content_type)

    monkeypatch.setattr(service.runtime.objects, "put", intercept)
    complete = record.model_copy(
        update={"status": "succeeded", "completed_at": datetime.now(UTC), "result_available": True}
    )
    if pruned:
        with pytest.raises(LeaseLost):
            await _save(store, lease, StoredToolExecution(complete, "late bytes"))
    else:
        await _save(store, lease, StoredToolExecution(complete, "late bytes"))
        assert (await store.get(lease.thread_id, record.execution_id)).record.status == "lost"
    assert (
        await service.runtime.objects.keys(f"tool-executions/{lease.workspace_id}/{record.execution_id}", limit=10)
        == []
    )


async def test_execution_inspection_api_scopes_pages_results_and_cancellation(service, scripted_model, runs_kit):
    started, lease = await _origin(service, scripted_model, runs_kit)
    store = Store(service.runtime)
    first, second = _record(lease, "exec-first"), _record(lease, "exec-second")
    for record in (first, second):
        await _save(store, lease, StoredToolExecution(record))
    base = f"{service.api}/threads/{lease.thread_id}/tool-executions"
    page = await service.client.get(base, params={"limit": 1, "run_id": lease.run_id})
    assert page.status_code == 200, page.text
    assert page.json()["items"][0]["run_id"] == lease.run_id
    assert page.json()["items"][0]["attempt_id"] == lease.attempt_id
    tail = await service.client.get(
        base, params={"limit": 1, "run_id": lease.run_id, "cursor": page.json()["next_cursor"]}
    )
    assert tail.status_code == 200, tail.text
    assert {page.json()["items"][0]["execution_id"], tail.json()["items"][0]["execution_id"]} == {
        first.execution_id,
        second.execution_id,
    }
    assert tail.json()["next_cursor"] is None
    denied = await service.client.get(base, headers={"X-Workspace-ID": "ws_" + "f" * 20})
    assert denied.status_code in {403, 404}
    other = await runs_kit.start_thread(service, {"id": started["run"]["agent_id"]}, "unrelated")
    wrong = f"{service.api}/threads/{other['thread']['id']}/tool-executions/{first.execution_id}"
    assert (await service.client.get(wrong)).status_code == 404
    assert (await service.client.post(wrong + "/cancel")).status_code == 404
    cancelled = await service.client.post(f"{base}/{first.execution_id}/cancel")
    assert cancelled.status_code == 202, cancelled.text
    assert cancelled.json()["cancel_requested_at"] is not None
    assert cancelled.json()["status"] == "queued"
    again = await service.client.post(f"{base}/{first.execution_id}/cancel")
    assert again.json()["cancel_requested_at"] == cancelled.json()["cancel_requested_at"]
    complete = second.model_copy(
        update={"status": "succeeded", "completed_at": datetime.now(UTC), "result_available": True}
    )
    await _save(store, lease, StoredToolExecution(complete, {"text": "x" * 5000}))
    result = await service.client.get(f"{base}/{second.execution_id}/result", params={"limit": 100})
    assert result.status_code == 200, result.text
    assert len(result.json()["text"]) == 100
    assert result.json()["has_more"]
    continued = await service.client.get(
        f"{base}/{second.execution_id}/result", params={"offset": 100, "version": result.json()["version"]}
    )
    assert continued.status_code == 200, continued.text
    assert not (await store.get(lease.thread_id, second.execution_id)).record.delivery_acknowledged
    active = await service.client.get(base, params={"active": True})
    assert [item["execution_id"] for item in active.json()["items"]] == [first.execution_id]
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(ToolExecutionRow, second.execution_id)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert (await service.client.get(f"{base}/{second.execution_id}")).status_code == 404
    assert (await service.client.get(f"{base}/{second.execution_id}/result")).status_code == 404
    schema = service.app.openapi()
    queries = [
        operation
        for path, methods in schema["paths"].items()
        if "/tool-executions" in path and not path.endswith("/observation")
        for method, operation in methods.items()
        if method == "get"
    ]
    assert len(queries) == 3
    assert all(operation.get("x-a13n-mcp") for operation in queries)
    cancel = schema["paths"]["/api/v1/threads/{thread_id}/tool-executions/{execution_id}/cancel"]["post"]
    assert not cancel.get("x-a13n-mcp")
    observe = schema["paths"]["/api/v1/threads/{thread_id}/tool-executions/{execution_id}/observation"]["get"]
    assert not observe.get("x-a13n-mcp")
