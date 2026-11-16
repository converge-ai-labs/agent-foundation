from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, replace
from typing import Any

import pytest
from a13n_harness import (
    AbstractHarnessPlugin,
    AgentContext,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessRunResult,
    HarnessRunResultEvent,
    HarnessState,
    ModelRecoveryPolicy,
    PluginError,
    RunBindings,
    RunCleanupError,
    SemanticRunInput,
)
from a13n_harness.environment.advanced import create_empty_environment_runtime
from a13n_harness.plugins import PluginRunExchange, PluginRunNext
from a13n_harness.usage import UsageSnapshot
from pydantic import BaseModel
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.tools import RunContext

pytestmark = pytest.mark.anyio


async def test_plugin_points_span_recovery_without_rebinding() -> None:
    log: list[str] = []
    contexts: list[AgentContext] = []
    model_inputs: list[list[ModelMessage]] = []

    class AttemptCapability(AbstractCapability[AgentContext]):
        async def before_run(self, ctx: RunContext[AgentContext]) -> None:
            assert ctx.deps is contexts[0]
            assert ctx.deps.plugins.require("inner", Plugin).bound
            log.append("attempt")

    @dataclass
    class Plugin(AbstractHarnessPlugin):
        name: str
        bound: bool = False

        @property
        def plugin_id(self) -> str:
            return self.name

        def for_agent(self) -> Plugin:
            log.append(f"agent:{self.plugin_id}")
            return self

        def get_capabilities(self) -> Sequence[AbstractCapability[AgentContext]]:
            log.append(f"capabilities:{self.plugin_id}")
            return (AttemptCapability(),) if self.plugin_id == "inner" else ()

        async def for_run(self, context: AgentContext) -> Plugin:
            log.append(f"bind:{self.plugin_id}")
            contexts.append(context)
            with pytest.raises(PluginError) as pending:
                _ = context.plugins.ordered
            assert pending.value.code == "plugins_not_bound"
            return replace(self, bound=True)

        async def wrap_run(self, exchange: PluginRunExchange, call_next: PluginRunNext[Any]) -> HarnessRunResult[Any]:
            assert exchange.context.plugins.require(self.plugin_id, Plugin) is self
            log.append(f"wrap:{self.plugin_id}")
            transformed = exchange.with_input(SemanticRunInput(f"{exchange.input.value}|{self.plugin_id}"))
            try:
                result = await call_next(transformed)
                log.append(f"result:{self.plugin_id}")
                return result.replace(output=f"{result.output}|{self.plugin_id}")
            finally:
                log.append(f"close:{self.plugin_id}")

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        model_inputs.append(messages)
        log.append("model")
        if len(model_inputs) == 1:
            yield "partial"
            raise ConnectionResetError("interrupted")
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        plugins=(Plugin("outer"), Plugin("inner")),
        model_recovery=ModelRecoveryPolicy(
            enabled=True, max_attempts=2, backoff_initial_seconds=0, backoff_max_seconds=0
        ),
    )
    build_log = ["agent:outer", "capabilities:outer", "agent:inner", "capabilities:inner"]
    assert log == build_log
    async with executable.stream("start") as stream:
        entry_log = [*build_log, "bind:outer", "bind:inner"]
        assert log == entry_log
        assert contexts == [stream.context, stream.context]
        assert stream.result is None
        assert model_inputs == []
        items = [item async for item in stream]

    assert log == [
        *entry_log,
        "wrap:outer",
        "wrap:inner",
        "attempt",
        "model",
        "attempt",
        "model",
        "result:inner",
        "close:inner",
        "result:outer",
        "close:outer",
    ]
    assert any(
        isinstance(message, ModelRequest)
        and any(isinstance(part, UserPromptPart) and part.content == "start|outer|inner" for part in message.parts)
        for message in model_inputs[0]
    )
    assert isinstance(items[-1], HarnessRunResultEvent)
    assert items[-1].result.output == "done|inner|outer"
    assert items[-1].result.usage.requests == 2
    assert stream.result == stream.outcome == items[-1].result


@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_live_checkpoints_remain_detached_from_plugin_result_and_cleanup(cleanup_fails: bool) -> None:
    class Value(BaseModel):
        value: int

    snapshots: list[HarnessState] = []
    initial = HarnessState.new(thread_id="thread_checkpoint")
    transferred: HarnessState | None = None

    class CheckpointPlugin(AbstractHarnessPlugin):
        plugin_id = "checkpoint"

        async def wrap_run(self, exchange: PluginRunExchange, call_next: PluginRunNext[Any]) -> HarnessRunResult[Any]:
            nonlocal transferred
            await exchange.context.state.write("test.value", Value(value=1), version="1")
            snapshots.append(await exchange.export_current_state())
            try:
                result = await call_next(exchange)
                await exchange.context.state.write("test.value", Value(value=2), version="1")
                snapshots.append(await exchange.export_current_state())
                transferred = HarnessState.new(
                    thread_id=exchange.context.thread_id, agent_context_state=snapshots[0].agent_context_state
                )
                return result.replace(state=transferred)
            finally:
                await exchange.context.state.write("test.value", Value(value=3), version="1")
                if cleanup_fails:
                    raise RuntimeError("checkpoint cleanup failed")

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), plugins=(CheckpointPlugin(),)
    )
    stream = executable.stream("start", previous_state=initial)
    items: list[HarnessEvent | HarnessRunResultEvent[str]] = []
    with pytest.raises(RunCleanupError) if cleanup_fails else nullcontext():
        async with stream:
            entered = await stream.export_state()
            snapshot = UsageSnapshot.from_state(entered)
            assert snapshot is not None and snapshot.records == ()
            assert entered.thread_id == initial.thread_id
            assert entered.message_history == initial.message_history
            assert set(entered.agent_context_state.entries) == {"a13n.usage"}
            assert initial.agent_context_state.entries == {}
            async for item in stream:
                items.append(item)

    assert [snapshot.agent_context_state.entries["test.value"].data for snapshot in snapshots] == [
        {"value": 1},
        {"value": 2},
    ]
    assert snapshots[0].message_history == ()
    assert snapshots[1].message_history
    assert initial.agent_context_state.entries == {}
    assert transferred is not None
    assert stream.outcome is not None
    assert await stream.export_state() == stream.outcome.state
    assert stream.outcome.all_messages() != transferred.message_history
    if cleanup_fails:
        # The replacement never returned: the nearest validated inner result remains authoritative.
        assert stream.outcome.state != transferred
        assert stream.outcome.state is not None
        assert stream.outcome.state.agent_context_state.entries["test.value"].data == {"value": 1}
        assert stream.result is None
        assert not any(isinstance(item, HarnessRunResultEvent) for item in items)
    else:
        assert stream.outcome.state == transferred
        assert isinstance(items[-1], HarnessRunResultEvent)
        assert stream.result == items[-1].result


@pytest.mark.parametrize("next_error", [None, RuntimeError("new failure"), asyncio.CancelledError("cancelled")])
async def test_handled_terminal_cleanup_failure_is_not_raised_again_on_context_exit(next_error) -> None:
    cleanup_failure = RuntimeError("terminal cleanup failed")

    class CleanupPlugin(AbstractHarnessPlugin):
        plugin_id = "cleanup"

        async def for_run(self, context: AgentContext) -> CleanupPlugin:
            async def close() -> None:
                raise cleanup_failure

            context._register_run_cleanup("test.cleanup", close)
            return self

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), plugins=(CleanupPlugin(),)
    )
    events = []
    with pytest.raises(type(next_error)) if next_error is not None else nullcontext() as raised:
        async with executable.stream("start") as stream:
            with pytest.raises(RunCleanupError) as caught:
                async for event in stream:
                    events.append(event)
            assert stream.outcome is not None
            assert stream.outcome.output == "done"
            assert caught.value.outcome is stream.outcome
            assert caught.value.causes == (cleanup_failure,)
            assert stream.result is None
            assert not any(isinstance(event, HarnessRunResultEvent) for event in events)
            if next_error is not None:
                raise next_error
    if next_error is not None:
        assert raised.value is next_error
    assert await stream.export_state() == stream.outcome.state


@pytest.mark.parametrize("phase", ["prepare", "execute", "body"])
@pytest.mark.parametrize("chain", ["explicit", "implicit", "suppressed"])
async def test_original_failure_survives_secondary_cleanup_failure(phase: str, chain: str) -> None:
    primary = ValueError("execution failed")
    root_cause = LookupError("original root cause")
    cleanup_failure = RuntimeError("cleanup failed")

    def fail() -> None:
        try:
            raise root_cause
        except LookupError:
            if chain == "explicit":
                raise primary from root_cause
            if chain == "suppressed":
                raise primary from None
            raise primary  # noqa: B904 - Exercise implicit exception chaining.

    class FailingPlugin(AbstractHarnessPlugin):
        plugin_id = "failure"

        async def for_run(self, context: AgentContext) -> FailingPlugin:
            async def close() -> None:
                raise cleanup_failure

            context._register_run_cleanup("test.failure", close)
            if phase == "prepare":
                fail()
            return self

        async def wrap_run(self, exchange: PluginRunExchange, call_next: PluginRunNext[Any]) -> HarnessRunResult[Any]:
            fail()
            raise AssertionError("unreachable")

    executable = HarnessBuilder().build(AgentSpec(), output_type=str, plugins=(FailingPlugin(),))
    stream = executable.stream("start")
    with pytest.raises(ValueError) as caught:
        async with stream:
            if phase == "body":
                fail()
            async for _event in stream:
                pass
    assert caught.value is primary
    assert stream.diagnostic_error is primary
    assert stream.outcome is None and stream.result is None
    secondary = primary.__cause__
    assert isinstance(secondary, RunCleanupError)
    assert secondary.outcome is None
    assert secondary.causes == (cleanup_failure,)
    assert secondary.__context__ is root_cause
    assert secondary.__suppress_context__ == (chain != "implicit")
    assert secondary.__cause__ is (root_cause if chain == "explicit" else None)
    assert await stream.export_state() is not None


async def test_environment_observer_failure_is_not_a_new_cleanup_failure() -> None:
    runtime = create_empty_environment_runtime()
    failure = OSError("environment observer failed")

    def observe(item: HarnessEvent) -> None:
        if isinstance(item.event, HarnessExtensionEvent) and item.event.payload.get("type") == "environment_changed":
            raise failure

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        await runtime.set_default(None)
        await asyncio.Event().wait()
        yield "unreachable"

    executable = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=model))
    stream = executable.stream("start", bindings=RunBindings.embedded(environment=runtime, producer_observer=observe))
    with pytest.raises(OSError) as caught:
        async with stream:
            async for _event in stream:
                pass
    assert caught.value is failure
    assert caught.value.__cause__ is None
    assert stream.diagnostic_error is failure
    assert stream.outcome is None and stream.result is None


async def test_primary_cleanup_error_retains_identity_and_original_causes() -> None:
    original_cause = OSError("original cleanup error")
    primary = RunCleanupError("original primary", outcome=None, causes=(original_cause,))
    secondary = ValueError("secondary cleanup error")

    class FailingPlugin(AbstractHarnessPlugin):
        plugin_id = "cleanup-error"

        async def for_run(self, context: AgentContext) -> FailingPlugin:
            async def close() -> None:
                raise secondary

            context._register_run_cleanup("test.cleanup-error", close)
            return self

        async def wrap_run(self, exchange: PluginRunExchange, call_next: PluginRunNext[Any]) -> HarnessRunResult[Any]:
            raise primary

    executable = HarnessBuilder().build(AgentSpec(), output_type=str, plugins=(FailingPlugin(),))
    with pytest.raises(RunCleanupError) as caught:
        await executable.run("start")
    assert caught.value is primary
    assert primary.causes == (original_cause,)
    assert primary.__cause__ is not primary
    assert isinstance(primary.__cause__, RunCleanupError)
    assert primary.__cause__.causes == (secondary,)
