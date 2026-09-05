"""Two real Harness Runs over fresh E2B adapters and one persistent native target."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_environment_provider import (
    E2BEnvironment,
    E2BProviderConfiguration,
    E2BProviderRuntime,
    EnvironmentAction,
    EnvironmentPermissionSet,
)
from a13n_environment_provider.e2b.commands import GuestCommands
from a13n_environment_provider.e2b.files import E2BFiles
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.environment import DynamicEnvironmentCapability, DynamicEnvironmentConfiguration
from a13n_harness.tools import InvocationPolicyCapability, InvocationPolicyDecision
from pydantic import SecretStr
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

pytestmark = pytest.mark.anyio


class NativeCommands:
    def __init__(self):
        self.running = False
        self.calls = []
        self.handles = []

    def attach(self):
        completion = asyncio.get_running_loop().create_future()

        async def wait():
            return await completion

        async def disconnect():
            self.calls.append("disconnect")
            completion.cancel()

        handle = SimpleNamespace(pid=42, wait=wait, disconnect=disconnect, completion=completion)
        self.handles.append(handle)
        return handle

    async def run(self, command, **kwargs):
        assert command == "sleep 60"
        assert kwargs["timeout"] == 0
        self.calls.append("run")
        self.running = True
        return self.attach()

    async def list(self, **kwargs):
        self.calls.append("list")
        return (
            [SimpleNamespace(pid=42, tag=None, envs={"SECRET": "private"}, args=["private-command"])]
            if self.running
            else []
        )

    async def connect(self, pid, **kwargs):
        assert pid == 42 and self.running
        self.calls.append("connect")
        return self.attach()

    async def kill(self, pid, **kwargs):
        assert pid == 42
        self.calls.append("kill")
        self.running = False
        for handle in self.handles:
            if not handle.completion.done():
                handle.completion.set_result(SimpleNamespace(exit_code=137))
        return True


@pytest.mark.parametrize("discovery_only", [False, True])
async def test_cross_run_discovery_authorization_lazy_observation_and_explicit_kill(monkeypatch, discovery_only):
    native = NativeCommands()
    sandbox = SimpleNamespace(sandbox_id="sandbox-persistent", commands=native)
    monkeypatch.setattr(E2BFiles, "stat", AsyncMock())
    monkeypatch.setattr(GuestCommands, "files", AsyncMock(return_value={"path": "/home/user"}))

    class NativeEnvironment(E2BEnvironment):
        async def _prepare(self, **kwargs):
            permissions = self.descriptor.permissions
            self._remember(sandbox.sandbox_id)
            await self._open_operations(sandbox, kwargs["mount_id"])
            self._descriptor = self._descriptor.model_copy(update={"permissions": permissions})

        async def _ensure_ready(self, operations):
            pass

    configuration = E2BProviderConfiguration()
    runtime = E2BProviderRuntime(SecretStr("test-only"))
    policy_resources = []

    async def allow(invocation, metadata, *, context):
        policy_resources.extend(invocation.resources)
        return InvocationPolicyDecision.allow()

    def bindings():
        return RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=allow),))

    results = []

    def executable(calls):
        index = 0

        async def stream(messages, info):
            nonlocal index
            returns = [
                part.content
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            ]
            if returns:
                results.append(returns[-1])
            if index >= len(calls):
                yield "done"
                return
            tool, args = calls[index](returns)
            index += 1
            assert tool in {definition.name for definition in info.function_tools}
            yield {0: DeltaToolCall(name=tool, json_args=json.dumps(args), tool_call_id=f"call-{index}")}

        return HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=stream),
            capabilities=(DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),),
        )

    first = NativeEnvironment(configuration, environment_id="env-test", state=None, runtime=runtime)
    await executable([lambda _: ("shell_exec", {"command": "sleep 60", "yield_time_seconds": 0})]).run(
        "start", environment=first, bindings=bindings()
    )
    assert native.running
    assert native.calls.count("run") == 1
    assert "disconnect" in native.calls
    assert "kill" not in native.calls
    old_ref = results[-1]["process_id"]
    state = first.dump_state()
    assert state is not None
    second = NativeEnvironment(configuration, environment_id="env-test", state=state, runtime=runtime)
    if discovery_only:
        second._descriptor = second.descriptor.model_copy(
            update={
                "permissions": EnvironmentPermissionSet(
                    operations=frozenset({EnvironmentAction.PROCESS_LIST, EnvironmentAction.PROCESS_INSPECT})
                ),
            }
        )
    selected = None

    def inspect(returns):
        nonlocal selected
        listing = returns[-1]
        assert listing["ok"]
        assert not any(call == "connect" for call in native.calls)
        selected = listing["processes"][0]["process_id"]
        assert selected != old_ref
        return "shell_info", {"process_id": selected}

    def wait(returns):
        assert returns[-1]["status"]["phase"] == "running"
        assert "connect" not in native.calls
        return "shell_wait", {"process_id": selected, "timeout_seconds": 0}

    def kill(returns):
        assert returns[-1]["stdout"]["origin"] == "sdk_text"
        assert returns[-1]["stdout"]["coverage"] == "partial"
        return "shell_signal", {"process_id": selected, "signal": "kill"}

    calls = [lambda _: ("shell_info", {}), inspect]
    if not discovery_only:
        calls.extend([wait, kill])
    await executable(calls).run("recover", environment=second, bindings=bindings())
    assert native.running is discovery_only
    assert native.calls.count("run") == 1
    assert native.calls.count("connect") == (0 if discovery_only else 1)
    assert native.calls.count("kill") == (0 if discovery_only else 1)
    assert "private" not in json.dumps(results)
    assert any(resource.kind == "mount" for resource in policy_resources)
    assert any(resource.kind == "managed-process" for resource in policy_resources)
