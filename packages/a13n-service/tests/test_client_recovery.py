"""Model-visible reconnection notices survive a checkpoint and require a real tool result."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.model_context import ModelContextProjection, ModelContextProjectionRequest, ModelContextRequestKind
from a13n_harness.state import AgentContextState
from a13n_service.runs.environments.clients import recovery
from a13n_service.runs.environments.clients.observations import Incarnation, current
from pydantic_ai.messages import ModelRequest, ToolCallPart, ToolReturnPart

pytestmark = pytest.mark.anyio


async def test_recovered_worker_reports_unknown_outcome_once_after_verified_reconnect(monkeypatch):
    online = SimpleNamespace(epoch="second", credential_version=1, descriptor=SimpleNamespace(device_id="dev"))
    presence = AsyncMock(return_value=None)
    runtime = SimpleNamespace(client_connections=SimpleNamespace(presence=presence))
    monkeypatch.setattr(recovery, "prove", AsyncMock())
    monkeypatch.setattr(recovery, "validate_attachment", AsyncMock())

    def owner():
        capability = recovery.Recovery(runtime, SimpleNamespace(run_id="original"), None, {}, {})
        capability.mounts["pc"] = None, SimpleNamespace(client=Incarnation("dev"), environment_id="env_test")
        return capability

    state = AgentContextState()
    ctx = SimpleNamespace(deps=SimpleNamespace(state=state), messages=[])
    call = ToolCallPart("shell_exec", {}, tool_call_id="call-lost")

    async def tool(_):
        current.get().disconnected["pc"] = "first"
        return {"ok": False, "error": {"code": "environment_unknown_outcome", "details": {"mount": "pc"}}}

    failed = await owner().wrap_tool_execute(ctx, call=call, tool_def=None, args={}, handler=tool)
    assert not presence.await_count
    ctx.deps.state = AgentContextState(await state.snapshot())
    ctx.messages = [ModelRequest(parts=[ToolReturnPart("shell_exec", failed, tool_call_id="call-lost")])]
    request = ModelContextProjectionRequest(kind=ModelContextRequestKind.TOOL_RESULTS, tool_call_ids=("call-lost",))
    project = AsyncMock(return_value=ModelContextProjection())
    assert not (await owner().wrap_model_context(ctx, request, project)).blocks
    presence.return_value = online
    ctx.deps.state = AgentContextState(await ctx.deps.state.snapshot())
    (notice,) = (await owner().wrap_model_context(ctx, request, project)).blocks
    assert "Client mount pc has reconnected" in notice.content
    assert "previous operation outcome is unknown" in notice.content
    ctx.deps.state = AgentContextState(await ctx.deps.state.snapshot())
    assert not (await owner().wrap_model_context(ctx, request, project)).blocks


async def test_healthy_tools_do_not_write_recovery_state():
    state = AgentContextState()
    state.write = AsyncMock(wraps=state.write)
    ctx = SimpleNamespace(deps=SimpleNamespace(state=state), messages=[])
    capability = recovery.Recovery(None, SimpleNamespace(run_id="healthy"), None, {}, {})

    async def tool(_):
        current.get().successes.add("pc")
        return {"ok": True}

    for index in range(3):
        call_id = f"healthy-{index}"
        result = await capability.wrap_tool_execute(
            ctx, call=ToolCallPart("shell_exec", {}, tool_call_id=call_id), tool_def=None, args={}, handler=tool
        )
        ctx.messages = [ModelRequest(parts=[ToolReturnPart("shell_exec", result, tool_call_id=call_id)])]
        request = ModelContextProjectionRequest(kind=ModelContextRequestKind.TOOL_RESULTS, tool_call_ids=(call_id,))
        assert not (
            await capability.wrap_model_context(ctx, request, AsyncMock(return_value=ModelContextProjection()))
        ).blocks
        assert not capability.successes
    state.write.assert_not_awaited()


@pytest.mark.parametrize("completion_order", [("success", "failure"), ("failure", "success")])
@pytest.mark.parametrize("result_order", [("success", "failure"), ("failure", "success")])
async def test_mixed_batch_retains_result_order_across_checkpoint(completion_order, result_order):
    runtime = SimpleNamespace(client_connections=SimpleNamespace(presence=AsyncMock(return_value=None)))
    capability = recovery.Recovery(runtime, SimpleNamespace(run_id="mixed"), None, {}, {})
    capability.mounts["pc"] = None, SimpleNamespace(client=Incarnation("dev"), environment_id="env_test")
    ctx = SimpleNamespace(deps=SimpleNamespace(state=AgentContextState()), messages=[])
    released = {name: asyncio.Event() for name in completion_order}

    async def execute(name):
        async def tool(_):
            await released[name].wait()
            if name == "success":
                current.get().successes.add("pc")
                return {"ok": True}
            current.get().disconnected["pc"] = "first"
            return {"ok": False, "error": {"code": "environment_unavailable", "details": {"mount": "pc"}}}

        return await capability.wrap_tool_execute(
            ctx, call=ToolCallPart("shell_exec", {}, tool_call_id=name), tool_def=None, args={}, handler=tool
        )

    tasks = {name: asyncio.create_task(execute(name)) for name in completion_order}
    results = {}
    for name in completion_order:
        released[name].set()
        results[name] = await tasks[name]
    # Recreate both the capability and its state: necessary success observations are durable.
    ctx.deps.state = AgentContextState(await ctx.deps.state.snapshot())
    resumed = recovery.Recovery(runtime, SimpleNamespace(run_id="mixed"), None, {}, {})
    resumed.mounts = capability.mounts
    ctx.messages = [
        ModelRequest(parts=[ToolReturnPart("shell_exec", results[name], tool_call_id=name) for name in result_order])
    ]
    request = ModelContextProjectionRequest(kind=ModelContextRequestKind.TOOL_RESULTS, tool_call_ids=result_order)
    await resumed.wrap_model_context(ctx, request, AsyncMock(return_value=ModelContextProjection()))
    state = await resumed._state(ctx)
    assert bool(state.mounts) == (result_order[-1] == "failure")
    assert not state.observations


async def test_success_clears_an_existing_recovery_cycle():
    state = AgentContextState()
    await state.write(
        recovery._NAMESPACE,
        recovery.State(run_id="pending", mounts={"pc": recovery.Cycle(call_id="lost")}),
        version="1",
    )
    ctx = SimpleNamespace(deps=SimpleNamespace(state=state), messages=[])
    capability = recovery.Recovery(None, SimpleNamespace(run_id="pending"), None, {}, {})

    async def tool(_):
        current.get().successes.add("pc")
        return {"ok": True}

    result = await capability.wrap_tool_execute(
        ctx, call=ToolCallPart("shell_exec", {}, tool_call_id="ok"), tool_def=None, args={}, handler=tool
    )
    ctx.messages = [ModelRequest(parts=[ToolReturnPart("shell_exec", result, tool_call_id="ok")])]
    request = ModelContextProjectionRequest(kind=ModelContextRequestKind.TOOL_RESULTS, tool_call_ids=("ok",))
    await capability.wrap_model_context(ctx, request, AsyncMock(return_value=ModelContextProjection()))
    assert not (await capability._state(ctx)).mounts
