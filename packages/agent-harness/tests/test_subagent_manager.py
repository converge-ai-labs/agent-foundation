from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_harness import (
    AgentDefinition,
    AgentIdentityRef,
    AgentInstanceContext,
    DefinitionError,
    HarnessBuilder,
    RunBindings,
    SubagentDefinition,
)
from a13n_harness.capabilities import (
    SubagentCapability,
    SubagentEvent,
    SubagentManager,
)
from a13n_harness.environment.advanced import EmptyEnvironmentRuntime
from a13n_harness.state import AgentContextState
from a13n_harness.toolsets.subagent_manager import (
    ManagedSubagentState,
    SubagentManagerState,
    _AsyncSubagentProjection,
)
from pydantic import BaseModel
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import UsageLimits

pytestmark = pytest.mark.anyio

_SUBAGENT_STATE_ID = "a13n.subagent-manager"
_SUBAGENT_STATE_VERSION = "1"


class _SteeringNotifier:
    def __init__(self, run: Any) -> None:
        self._run = run
        self.notifications: list[tuple[str, str, tuple[str, ...]]] = []

    async def notify(self, message: str, *, source: str, references: tuple[str, ...]) -> str:
        self.notifications.append((message, source, references))
        return self._run.enqueue(message, priority="asap")


class _RunContext:
    def __init__(
        self,
        state: AgentContextState,
        *,
        thread_id: str = "thread-parent",
        run_id: str = "run-parent",
    ) -> None:
        self.enqueued: list[tuple[str, str]] = []
        self.deps = SimpleNamespace(
            state=state,
            thread_id=thread_id,
            run_id=run_id,
            instance=SimpleNamespace(
                agent_instance_id="agent-parent",
                host_refs={"session_id": "session-parent"},
            ),
            _steering=_SteeringNotifier(self),
        )

    def enqueue(self, value: str, *, priority: str) -> str:
        self.enqueued.append((value, priority))
        return f"enqueue-{len(self.enqueued)}"


class _ChildRuntime:
    def __init__(self) -> None:
        self.started: dict[str, asyncio.Event] = {}
        self.release: dict[str, asyncio.Event] = {}

    def block(self, prompt: str) -> tuple[asyncio.Event, asyncio.Event]:
        started = self.started.setdefault(prompt, asyncio.Event())
        release = self.release.setdefault(prompt, asyncio.Event())
        return started, release

    async def model(
        self,
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        raw_prompt = _latest_user_text(messages)
        known_prompts = (
            "off-run",
            "long-output",
            "control",
            "quick",
            "continued",
            "closing",
            "forked",
            "activity",
        )
        matched = tuple(value for value in known_prompts if value in raw_prompt)
        prompt = max(matched, key=raw_prompt.rfind) if matched else raw_prompt
        if prompt in self.release:
            self.started[prompt].set()
            await self.release[prompt].wait()
        tool_returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "inspect_step"
        ]
        if prompt == "activity" and not tool_returns:
            yield {
                0: DeltaToolCall(
                    name="inspect_step",
                    json_args=json.dumps({"path": "src/lifecycle.py", "token": "secret-value"}),
                    tool_call_id="activity-call",
                )
            }
        elif prompt == "activity":
            yield "activity complete Bearer secret-value"
        elif prompt == "long-output":
            yield "x" * (300 * 1024)
        else:
            yield f"result:{prompt}"


class _ControlledCleanupScopeFactory:
    def __init__(self, *, fail: bool = False) -> None:
        self.cleanup_started = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.exited = asyncio.Event()
        self.fail = fail

    @asynccontextmanager
    async def __call__(
        self,
        context: Any,
        child: Any,
        input: Any,
        child_instance_id: str,
        continuation: bool,
        usage_limits: UsageLimits | None,
    ):
        del child, input, continuation, usage_limits
        try:
            yield RunBindings(
                instance=AgentInstanceContext(
                    identity=AgentIdentityRef(issuer="test", subject="child"),
                    agent_instance_id="controlled-child",
                    parent_agent_instance_id=context.instance.agent_instance_id,
                    delegation_id=child_instance_id,
                ),
                environment=EmptyEnvironmentRuntime(),
            )
        finally:
            self.cleanup_started.set()
            await self.cleanup_release.wait()
            self.exited.set()
            if self.fail:
                raise RuntimeError("controlled cleanup failure")


class _ChildScopeFactory:
    def __init__(self) -> None:
        self.entered: list[tuple[str, bool, UsageLimits | None]] = []
        self.exited: list[str] = []

    @asynccontextmanager
    async def __call__(
        self,
        context: Any,
        child: Any,
        input: Any,
        child_instance_id: str,
        continuation: bool,
        usage_limits: UsageLimits | None,
    ):
        del child, input
        self.entered.append((child_instance_id, continuation, usage_limits))
        try:
            yield RunBindings(
                instance=AgentInstanceContext(
                    identity=AgentIdentityRef(issuer="test", subject="child"),
                    agent_instance_id=f"child-{len(self.entered)}",
                    parent_agent_instance_id=context.instance.agent_instance_id,
                    delegation_id=child_instance_id,
                ),
                environment=EmptyEnvironmentRuntime(),
            )
        finally:
            self.exited.append(child_instance_id)


def _latest_user_text(messages: list[ModelMessage]) -> str:
    return "\n".join(
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    )


def _built_parent(
    runtime: _ChildRuntime,
    *,
    capability: SubagentCapability | None = None,
    child_capabilities: tuple[Any, ...] = (),
):
    child = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="child-v1",
        model=FunctionModel(stream_function=runtime.model),
        capabilities=child_capabilities,
    )

    async def parent_model(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str]:
        del messages, info
        yield "parent"

    return HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            definition_id="parent-v1",
            model=FunctionModel(stream_function=parent_model),
            capabilities=(() if capability is None else (capability,)),
            subagents=(
                SubagentDefinition(
                    name="reviewer",
                    description="Review one bounded task.",
                    agent=child,
                    usage_limits=UsageLimits(request_limit=5),
                ),
            ),
        )
    )


async def _stored_state(state: AgentContextState) -> SubagentManagerState:
    stored = await state.read(
        _SUBAGENT_STATE_ID,
        SubagentManagerState,
        version=_SUBAGENT_STATE_VERSION,
    )
    assert stored is not None
    return stored


async def _wait_for(predicate: Callable[[], bool]) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), 2)


async def test_default_manager_keeps_canonical_children_across_runs_and_always_fires_hooks() -> None:
    runtime = _ChildRuntime()
    started, release = runtime.block("off-run")
    bindings = _ChildScopeFactory()
    events: list[SubagentEvent] = []

    def broken_hook(event: SubagentEvent) -> Any:
        del event
        raise RuntimeError("host observer failed")

    async def stable_hook(event: SubagentEvent) -> None:
        events.append(event)

    operator = SubagentManager(
        bindings,
        usage_limits=UsageLimits(request_limit=3),
        event_hooks=(broken_hook, stable_hook),
    )
    parent = _built_parent(runtime)
    state = AgentContextState()
    first = _AsyncSubagentProjection(operator=operator, children=parent.subagents)
    first_run = _RunContext(state, run_id="run-1")
    async with first.active_run(cast(Any, first_run)):
        accepted = await first.delegate(subagent_name="reviewer", prompt="off-run")
        await asyncio.wait_for(started.wait(), 2)

    assert accepted["execution_id"] == "subagent-1"
    assert accepted["status"] == "running"
    assert first_run.enqueued == []
    release.set()
    await _wait_for(lambda: [event.kind for event in events].count("completion") == 1)

    portable = await _stored_state(state)
    assert portable.subagents["subagent-1"].status == "running"
    assert [event.kind for event in events] == ["started", "completion"]
    assert events[1].thread_id == "thread-parent"
    assert events[1].run_id == "run-1"
    assert events[1].agent_instance_id == "agent-parent"
    assert events[1].host_refs == {"session_id": "session-parent"}
    assert events[0].child_thread_id is None
    assert events[1].subagent_id == "subagent-1"
    assert events[1].subagent_name == "reviewer"
    assert events[1].child_thread_id is not None
    assert events[1].usage.requests == 1

    second = _AsyncSubagentProjection(operator=operator, children=parent.subagents)
    second_run = _RunContext(state, run_id="run-2")
    async with second.active_run(cast(Any, second_run)):
        info = await second.info(
            "subagent-1",
            offset=0,
            limit=20,
        )
        long_execution = await second.delegate(subagent_name="reviewer", prompt="long-output")
        long_info = await second.wait(
            cast(str, long_execution["execution_id"]),
            timeout_seconds=2,
            offset=0,
            limit=20,
        )
        await _wait_for(lambda: [event.kind for event in events].count("completion") == 2)

    assert info["status"] == "succeeded"
    assert info["thread_id"] == events[1].child_thread_id
    assert info["usage"]["requests"] == 1
    assert "output" not in info
    assert long_execution["execution_id"] == "subagent-2"
    assert long_info["output"] == "x" * (300 * 1024)
    assert long_info["usage"]["requests"] == 1
    assert events[-1].child_thread_id == long_info["thread_id"]
    assert events[-1].usage.requests == 1
    assert len(second_run.enqueued) == 2
    assert "subagent-1 has finished" in second_run.enqueued[0][0]
    assert "subagent-2 has finished" in second_run.enqueued[1][0]
    assert all(priority == "asap" for _, priority in second_run.enqueued)
    assert second_run.deps._steering.notifications[0][1:] == ("async_subagent", ("subagent-1",))
    assert second_run.deps._steering.notifications[1][1:] == ("async_subagent", ("subagent-2",))

    stored = await _stored_state(state)
    assert stored.owner_thread_id == "thread-parent"
    assert stored.next_sequence == 3
    assert tuple(stored.subagents) == ("subagent-1", "subagent-2")
    assert set(stored.subagents["subagent-1"].model_dump()) == {
        "subagent_id",
        "subagent_name",
        "child_definition_id",
        "backend_id",
        "prompt",
        "status",
        "resumed_from",
        "failure",
        "resumable",
        "thread_id",
    }
    assert bindings.entered[0][2] is not None
    assert bindings.entered[0][2].request_limit == 3
    await operator.force_close()
    assert len(bindings.exited) == 2


async def test_default_manager_rejects_reused_child_authority_identity() -> None:
    runtime = _ChildRuntime()
    exited: list[str] = []

    @asynccontextmanager
    async def reused_scope(
        context: Any,
        child: Any,
        input: Any,
        child_instance_id: str,
        continuation: bool,
        usage_limits: UsageLimits | None,
    ):
        del child, input, continuation, usage_limits
        try:
            yield RunBindings(
                instance=AgentInstanceContext(
                    identity=AgentIdentityRef(issuer="test", subject="child"),
                    agent_instance_id="reused-child",
                    parent_agent_instance_id=context.instance.agent_instance_id,
                    delegation_id=child_instance_id,
                ),
                environment=EmptyEnvironmentRuntime(),
            )
        finally:
            exited.append(child_instance_id)

    operator = SubagentManager(reused_scope)
    parent = _built_parent(runtime)
    state = AgentContextState()
    projection = _AsyncSubagentProjection(operator=operator, children=parent.subagents)
    run = _RunContext(state)

    async with projection.active_run(cast(Any, run)):
        accepted = await projection.delegate(subagent_name="reviewer", prompt="quick")
        await projection.wait("subagent-1", timeout_seconds=2, offset=0, limit=20)
        with pytest.raises(DefinitionError, match="reused an existing authority identity"):
            await projection.delegate(subagent_name="reviewer", prompt="quick")

    assert accepted == {"execution_id": "subagent-1", "status": "running"}
    assert len(exited) == 2
    assert tuple((await _stored_state(state)).subagents) == ("subagent-1",)
    await operator.force_close()


async def test_subagent_projection_does_not_refresh_after_committed_admission() -> None:
    class SnapshotFailureManager(SubagentManager):
        async def snapshot(self, context: Any, backend_id: str):
            del context, backend_id
            raise RuntimeError("post-admission snapshot failed")

    runtime = _ChildRuntime()
    started, _ = runtime.block("control")
    operator = SnapshotFailureManager(_ChildScopeFactory())
    parent = _built_parent(runtime)
    state = AgentContextState()
    projection = _AsyncSubagentProjection(operator=operator, children=parent.subagents)
    run = _RunContext(state)

    async with projection.active_run(cast(Any, run)):
        accepted = await projection.delegate(subagent_name="reviewer", prompt="control")
        await asyncio.wait_for(started.wait(), 2)

    assert accepted == {"execution_id": "subagent-1", "status": "running"}
    assert tuple((await _stored_state(state)).subagents) == ("subagent-1",)
    await operator.force_close()


async def test_subagent_info_lists_status_but_single_detail_includes_bounded_activity() -> None:
    runtime = _ChildRuntime()
    tool_started = asyncio.Event()
    tool_release = asyncio.Event()

    async def inspect_step(path: str, token: str) -> dict[str, str]:
        tool_started.set()
        await tool_release.wait()
        return {"path": path, "token": token, "content": "checked"}

    child_tools = Capability(
        toolsets=[FunctionToolset([inspect_step], id="activity-tools")],
        id="activity-capability",
    )
    operator = SubagentManager(_ChildScopeFactory())
    parent = _built_parent(runtime, child_capabilities=(child_tools,))
    state = AgentContextState()
    projection = _AsyncSubagentProjection(operator=operator, children=parent.subagents)
    run = _RunContext(state)

    async with projection.active_run(cast(Any, run)):
        accepted = await projection.delegate(subagent_name="reviewer", prompt="activity")
        await asyncio.wait_for(tool_started.wait(), 2)

        listed = await projection.info(None, offset=0, limit=20)
        running = await projection.info("subagent-1", offset=0, limit=20)
        tool_release.set()
        result = await projection.wait(
            "subagent-1",
            timeout_seconds=2,
            offset=0,
            limit=20,
        )
        completed = await projection.info("subagent-1", offset=0, limit=20)

    assert accepted == {"execution_id": "subagent-1", "status": "running"}
    assert listed["executions"][0]["status"] == "running"
    assert "input" not in listed["executions"][0]
    assert "activity" not in listed["executions"][0]
    assert running["input"] == "activity"
    assert running["activity"]["active_tool_calls"] == [
        {
            "tool_call_id": "activity-call",
            "tool_name": "inspect_step",
            "status": "running",
            "arguments": {"path": "src/lifecycle.py", "token": "[REDACTED]"},
            "result": None,
        }
    ]
    assert running["activity"]["recent_tool_calls"] == []
    assert result["output"] == "activity complete Bearer secret-value"
    assert completed["activity"]["active_tool_calls"] == []
    assert completed["activity"]["recent_tool_calls"] == [
        {
            "tool_call_id": "activity-call",
            "tool_name": "inspect_step",
            "status": "success",
            "arguments": {"path": "src/lifecycle.py", "token": "[REDACTED]"},
            "result": {"path": "src/lifecycle.py", "token": "[REDACTED]", "content": "checked"},
        }
    ]
    assert completed["activity"]["output_preview"] == "activity complete Bearer [REDACTED]"
    assert "output" not in completed

    portable = await _stored_state(state)
    projected = portable.subagents["subagent-1"].model_dump(mode="json")
    assert projected["prompt"] == "activity"
    assert "activity" not in projected
    assert "output" not in projected
    await operator.force_close()


async def test_default_manager_supports_steer_cancel_resume_and_close_drains_children() -> None:
    runtime = _ChildRuntime()
    control_started, _ = runtime.block("control")
    closing_started, _ = runtime.block("closing")
    bindings = _ChildScopeFactory()
    operator = SubagentManager(bindings)
    parent = _built_parent(runtime)
    state = AgentContextState()
    projection = _AsyncSubagentProjection(operator=operator, children=parent.subagents)
    run = _RunContext(state)

    async with projection.active_run(cast(Any, run)):
        controlled = await projection.delegate(subagent_name="reviewer", prompt="control")
        await asyncio.wait_for(control_started.wait(), 2)
        steered = await projection.steer("subagent-1", "new direction")
        cancelled = await projection.cancel("subagent-1")
        cancelled_info = await projection.wait(
            "subagent-1",
            timeout_seconds=2,
            offset=0,
            limit=20,
        )

        completed = await projection.delegate(subagent_name="reviewer", prompt="quick")
        completed_info = await projection.wait(
            "subagent-2",
            timeout_seconds=2,
            offset=0,
            limit=20,
        )
        resumed = await projection.resume("subagent-2", "continued")
        resumed_info = await projection.wait(
            "subagent-3",
            timeout_seconds=2,
            offset=0,
            limit=20,
        )

        closing = await projection.delegate(subagent_name="reviewer", prompt="closing")
        await asyncio.wait_for(closing_started.wait(), 2)

    assert controlled["execution_id"] == "subagent-1"
    assert steered["execution_id"] == "subagent-1"
    assert isinstance(steered["enqueue_id"], str)
    assert cancelled == {"execution_id": "subagent-1", "accepted": True}
    assert cancelled_info["status"] == "cancelled"
    assert completed["execution_id"] == "subagent-2"
    assert completed_info["status"] == "succeeded"
    assert completed_info["output"] == "result:quick"
    assert resumed["execution_id"] == "subagent-3"
    assert resumed["status"] == "running"
    assert resumed_info["status"] == "succeeded"
    assert resumed_info["resumed_from"] == "subagent-2"
    assert resumed_info["output"] == "result:continued"
    assert closing["execution_id"] == "subagent-4"
    assert [continuation for _, continuation, _ in bindings.entered] == [False, False, True, False]

    await operator.force_close()
    await operator.force_close()

    assert len(bindings.exited) == 4
    assert set(bindings.exited) == {entry[0] for entry in bindings.entered}


async def test_restored_subagent_state_is_lost_but_forked_owner_invalidates_refs() -> None:
    runtime = _ChildRuntime()
    parent = _built_parent(runtime)
    state = AgentContextState()
    await state.write(
        _SUBAGENT_STATE_ID,
        SubagentManagerState(
            owner_thread_id="thread-original",
            next_sequence=2,
            subagents={
                "subagent-1": ManagedSubagentState(
                    subagent_id="subagent-1",
                    subagent_name="reviewer",
                    child_definition_id="child-v1",
                    backend_id="missing-backend",
                    prompt="restored task",
                    status="running",
                )
            },
        ),
        version=_SUBAGENT_STATE_VERSION,
    )
    fork_state = AgentContextState(await state.snapshot())

    restored_operator = SubagentManager(_ChildScopeFactory())
    restored = _AsyncSubagentProjection(operator=restored_operator, children=parent.subagents)
    restored_run = _RunContext(state, thread_id="thread-original")
    async with restored.active_run(cast(Any, restored_run)):
        info = await restored.info(
            "subagent-1",
            offset=0,
            limit=20,
        )

    restored_state = await _stored_state(state)
    assert info["status"] == "lost"
    assert info["failure"] == {"code": "subagent_backend_lost"}
    assert restored_state.subagents["subagent-1"].backend_id == "missing-backend"
    assert "can no longer be observed" in restored_run.enqueued[0][0]

    fork_bindings = _ChildScopeFactory()
    fork_operator = SubagentManager(fork_bindings)
    forked = _AsyncSubagentProjection(operator=fork_operator, children=parent.subagents)
    fork_run = _RunContext(fork_state, thread_id="thread-fork")
    async with forked.active_run(cast(Any, fork_run)):
        empty = await forked.info(
            None,
            offset=0,
            limit=20,
        )
        started = await forked.delegate(subagent_name="reviewer", prompt="forked")
        result = await forked.wait(
            "subagent-2",
            timeout_seconds=2,
            offset=0,
            limit=20,
        )

    forked_state = await _stored_state(fork_state)
    assert empty["executions"] == []
    assert started["execution_id"] == "subagent-2"
    assert result["status"] == "succeeded"
    assert forked_state.owner_thread_id == "thread-fork"
    assert tuple(forked_state.subagents) == ("subagent-2",)
    await restored_operator.force_close()
    await fork_operator.force_close()


async def test_default_manager_rejects_invalid_fresh_child_lineage_and_closes_scope() -> None:
    runtime = _ChildRuntime()
    exited = asyncio.Event()

    @asynccontextmanager
    async def invalid_scope(
        context: Any,
        child: Any,
        input: Any,
        child_instance_id: str,
        continuation: bool,
        usage_limits: UsageLimits | None,
    ):
        del child, input, child_instance_id, continuation, usage_limits
        try:
            yield RunBindings(
                instance=AgentInstanceContext(
                    identity=AgentIdentityRef(issuer="test", subject="child"),
                    agent_instance_id=context.instance.agent_instance_id,
                    parent_agent_instance_id=context.instance.agent_instance_id,
                    delegation_id="wrong-delegation",
                ),
                environment=EmptyEnvironmentRuntime(),
            )
        finally:
            exited.set()

    operator = SubagentManager(invalid_scope)
    parent = _built_parent(runtime)
    projection = _AsyncSubagentProjection(operator=operator, children=parent.subagents)
    run = _RunContext(AgentContextState())

    async with projection.active_run(cast(Any, run)):
        with pytest.raises(DefinitionError, match="requested parent lineage") as exc_info:
            await projection.delegate(subagent_name="reviewer", prompt="quick")

    assert getattr(exc_info.value, "code", None) == "delegation_lineage_invalid"
    assert exited.is_set()
    await operator.force_close()


async def test_terminal_snapshot_is_published_only_after_child_scope_cleanup() -> None:
    runtime = _ChildRuntime()
    bindings = _ControlledCleanupScopeFactory(fail=True)
    operator = SubagentManager(bindings)
    parent = _built_parent(runtime)
    projection = _AsyncSubagentProjection(operator=operator, children=parent.subagents)
    run = _RunContext(AgentContextState())

    async with projection.active_run(cast(Any, run)):
        accepted = await projection.delegate(subagent_name="reviewer", prompt="quick")
        await asyncio.wait_for(bindings.cleanup_started.wait(), 2)
        before_cleanup = await projection.wait(
            "subagent-1",
            timeout_seconds=0.01,
            offset=0,
            limit=20,
        )
        bindings.cleanup_release.set()
        after_cleanup = await projection.wait(
            "subagent-1",
            timeout_seconds=2,
            offset=0,
            limit=20,
        )

    assert accepted == {"execution_id": "subagent-1", "status": "running"}
    assert before_cleanup["status"] == "running"
    assert after_cleanup["status"] == "failed"
    assert after_cleanup["output"] is None
    assert after_cleanup["failure"]["code"] == "subagent_binding_cleanup_failed"
    await operator.force_close()


async def test_slow_stable_hook_does_not_gate_child_execution_or_active_observer() -> None:
    runtime = _ChildRuntime()
    hook_started = asyncio.Event()
    hook_release = asyncio.Event()

    async def slow_hook(event: SubagentEvent) -> None:
        if event.kind == "started":
            hook_started.set()
            await hook_release.wait()

    operator = SubagentManager(_ChildScopeFactory(), event_hooks=(slow_hook,))
    parent = _built_parent(runtime)
    projection = _AsyncSubagentProjection(operator=operator, children=parent.subagents)
    run = _RunContext(AgentContextState())

    async with projection.active_run(cast(Any, run)):
        accepted = await asyncio.wait_for(
            projection.delegate(subagent_name="reviewer", prompt="quick"),
            0.5,
        )
        await asyncio.wait_for(hook_started.wait(), 2)
        completed = await projection.wait(
            "subagent-1",
            timeout_seconds=2,
            offset=0,
            limit=20,
        )

    assert accepted["status"] == "running"
    assert completed["status"] == "succeeded"
    assert run.enqueued and "subagent-1 has finished" in run.enqueued[0][0]
    hook_release.set()
    await operator.force_close()


async def test_projection_cancels_backend_when_parent_state_admission_fails() -> None:
    class FailingState(AgentContextState):
        async def write(
            self,
            capability_id: str,
            value: BaseModel,
            *,
            version: str,
        ) -> None:
            del value, version
            if capability_id == _SUBAGENT_STATE_ID:
                raise RuntimeError("state write failed")
            raise AssertionError("unexpected state namespace")

    runtime = _ChildRuntime()
    runtime.block("off-run")
    bindings = _ChildScopeFactory()
    operator = SubagentManager(bindings)
    parent = _built_parent(runtime)
    projection = _AsyncSubagentProjection(operator=operator, children=parent.subagents)
    run = _RunContext(FailingState())

    async with projection.active_run(cast(Any, run)):
        with pytest.raises(RuntimeError, match="state write failed"):
            await projection.delegate(subagent_name="reviewer", prompt="off-run")
        backend_id = bindings.entered[0][0]
        snapshot = await operator.wait(cast(Any, run.deps), backend_id, 2)

    assert snapshot is not None
    assert snapshot.status == "cancelled"
    assert bindings.exited == [backend_id]
    await operator.force_close()


async def test_subagent_admission_compensation_survives_repeated_cancellation() -> None:
    class FailingState(AgentContextState):
        async def write(
            self,
            capability_id: str,
            value: BaseModel,
            *,
            version: str,
        ) -> None:
            del value, version
            if capability_id == _SUBAGENT_STATE_ID:
                raise RuntimeError("state write failed")
            raise AssertionError("unexpected state namespace")

    class BlockingCancelManager(SubagentManager):
        def __init__(self, bindings: _ChildScopeFactory) -> None:
            super().__init__(bindings)
            self.cancel_started = asyncio.Event()
            self.cancel_release = asyncio.Event()

        async def cancel(self, context: Any, backend_id: str) -> bool:
            self.cancel_started.set()
            await self.cancel_release.wait()
            return await super().cancel(context, backend_id)

    runtime = _ChildRuntime()
    runtime.block("control")
    operator = BlockingCancelManager(_ChildScopeFactory())
    parent = _built_parent(runtime)
    projection = _AsyncSubagentProjection(operator=operator, children=parent.subagents)
    run = _RunContext(FailingState())

    async with projection.active_run(cast(Any, run)):
        starting = asyncio.create_task(projection.delegate(subagent_name="reviewer", prompt="control"))
        await asyncio.wait_for(operator.cancel_started.wait(), 2)
        starting.cancel()
        await asyncio.sleep(0)
        starting.cancel()
        await asyncio.sleep(0)
        assert not starting.done()
        operator.cancel_release.set()
        with pytest.raises(asyncio.CancelledError):
            await starting

    await operator.force_close()


async def test_subagent_manager_force_close_finishes_cleanup_before_propagating_cancellation() -> None:
    runtime = _ChildRuntime()
    bindings = _ControlledCleanupScopeFactory()
    operator = SubagentManager(bindings)
    parent = _built_parent(runtime)
    projection = _AsyncSubagentProjection(operator=operator, children=parent.subagents)
    run = _RunContext(AgentContextState())

    async with projection.active_run(cast(Any, run)):
        await projection.delegate(subagent_name="reviewer", prompt="quick")
        await asyncio.wait_for(bindings.cleanup_started.wait(), 2)

    closing = asyncio.create_task(operator.force_close())
    await asyncio.sleep(0)
    closing.cancel()
    await asyncio.sleep(0)
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done()

    bindings.cleanup_release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert bindings.exited.is_set()
    await operator.force_close()


async def test_subagent_manager_force_close_reports_binding_cleanup_failure() -> None:
    runtime = _ChildRuntime()
    bindings = _ControlledCleanupScopeFactory(fail=True)
    operator = SubagentManager(bindings)
    parent = _built_parent(runtime)
    projection = _AsyncSubagentProjection(operator=operator, children=parent.subagents)
    run = _RunContext(AgentContextState())

    async with projection.active_run(cast(Any, run)):
        await projection.delegate(subagent_name="reviewer", prompt="quick")
        await asyncio.wait_for(bindings.cleanup_started.wait(), 2)

    closing = asyncio.create_task(operator.force_close())
    await asyncio.sleep(0)
    bindings.cleanup_release.set()
    with pytest.raises(ExceptionGroup, match="Subagent binding cleanup failed"):
        await closing
    assert bindings.exited.is_set()


async def test_subagent_manager_force_close_matching_releases_only_selected_host_work() -> None:
    runtime = _ChildRuntime()
    selected_started, _selected_release = runtime.block("control")
    retained_started, retained_release = runtime.block("closing")
    bindings = _ChildScopeFactory()
    operator = SubagentManager(bindings)
    parent = _built_parent(runtime)
    selected_projection = _AsyncSubagentProjection(operator=operator, children=parent.subagents)
    retained_projection = _AsyncSubagentProjection(operator=operator, children=parent.subagents)
    selected_run = _RunContext(AgentContextState())
    retained_run = _RunContext(AgentContextState(), thread_id="thread-other", run_id="run-other")
    retained_run.deps.instance.host_refs = {"session_id": "session-other"}

    async with selected_projection.active_run(cast(Any, selected_run)):
        await selected_projection.delegate(subagent_name="reviewer", prompt="control")
    async with retained_projection.active_run(cast(Any, retained_run)):
        await retained_projection.delegate(subagent_name="reviewer", prompt="closing")
    await asyncio.wait_for(selected_started.wait(), 2)
    await asyncio.wait_for(retained_started.wait(), 2)

    await operator.force_close_matching({"session_id": "session-parent"})

    selected_backend = bindings.entered[0][0]
    retained_backend = bindings.entered[1][0]
    assert await operator.snapshot(cast(Any, selected_run.deps), selected_backend) is None
    retained = await operator.snapshot(cast(Any, retained_run.deps), retained_backend)
    assert retained is not None
    assert retained.status == "running"
    assert bindings.exited == [selected_backend]

    retained_release.set()
    await operator.force_close()
    assert set(bindings.exited) == {selected_backend, retained_backend}


async def test_managed_subagent_state_accepts_full_operator_backend_id_bound() -> None:
    state = ManagedSubagentState(
        subagent_id="subagent-1",
        subagent_name="reviewer",
        child_definition_id="child-v1",
        backend_id="b" * 512,
        prompt="bounded task",
        status="running",
    )

    assert len(state.backend_id) == 512


async def test_async_subagent_capability_exposes_fixed_standard_tool_schema() -> None:
    runtime = _ChildRuntime()
    bindings = _ChildScopeFactory()
    operator = SubagentManager(bindings)
    schemas: dict[str, dict[str, Any]] = {}

    async def parent_model(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str]:
        del messages
        schemas.update({tool.name: tool.parameters_json_schema for tool in info.function_tools})
        yield "done"

    child = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="child-v1",
        model=FunctionModel(stream_function=runtime.model),
    )
    executable = HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            definition_id="parent-v1",
            model=FunctionModel(stream_function=parent_model),
            capabilities=(SubagentCapability(execution="async", operator=operator),),
            subagents=(
                SubagentDefinition(
                    name="reviewer",
                    description="Review one bounded task.",
                    agent=child,
                ),
            ),
        )
    )

    result = await executable.run(
        "inspect tools",
        bindings=RunBindings(
            instance=AgentInstanceContext(
                identity=AgentIdentityRef(issuer="test", subject="parent"),
                agent_instance_id="parent-1",
            ),
            environment=EmptyEnvironmentRuntime(),
        ),
    )

    assert result.output_or_raise() == "done"
    assert tuple(schemas) == (
        "delegate",
        "subagent_info",
        "wait_subagent",
        "steer_subagent",
        "cancel_subagent",
        "resume_subagent",
    )
    assert schemas["delegate"]["required"] == ["subagent_name", "prompt"]
    assert schemas["delegate"]["properties"]["subagent_name"] == {
        "description": "Exact declared subagent name",
        "maxLength": 63,
        "pattern": "^[a-z][a-z0-9_-]{0,62}$",
        "type": "string",
    }
    assert schemas["delegate"]["properties"]["prompt"]["description"] == (
        "Bounded task, constraints, and expected result"
    )
    for tool_name in (
        "delegate",
        "subagent_info",
        "wait_subagent",
        "steer_subagent",
        "cancel_subagent",
        "resume_subagent",
    ):
        assert schemas[tool_name]["additionalProperties"] is False
        execution = schemas[tool_name]["properties"].get("execution_id")
        if execution is not None:
            candidate = execution["anyOf"][0] if "anyOf" in execution else execution
            assert candidate["pattern"] == "^subagent-[1-9][0-9]*$"
            assert candidate["maxLength"] == 24

    info_properties = schemas["subagent_info"]["properties"]
    assert tuple(info_properties) == ("execution_id", "execution_offset", "execution_limit")
    assert info_properties["execution_id"]["description"] == (
        "Execution ID for bounded detail; omit to list status summaries"
    )
    assert info_properties["execution_offset"]["description"].endswith("used only when execution_id is omitted")
    assert info_properties["execution_limit"]["default"] == 20
    assert info_properties["execution_limit"]["maximum"] == 100

    wait_properties = schemas["wait_subagent"]["properties"]
    assert tuple(wait_properties) == (
        "execution_id",
        "timeout_seconds",
        "execution_offset",
        "execution_limit",
    )
    assert wait_properties["execution_id"]["description"] == (
        "Execution ID for a complete result; omit to fan in current executions"
    )
    timeout = wait_properties["timeout_seconds"]["anyOf"][0]
    assert timeout["exclusiveMinimum"] == 0
    assert timeout["maximum"] == 300
    assert "required" not in schemas["subagent_info"]
    assert "required" not in schemas["wait_subagent"]
    await operator.force_close()
