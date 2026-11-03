"""Model-visible reconnection notices survive a checkpoint and require a real tool result."""

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
