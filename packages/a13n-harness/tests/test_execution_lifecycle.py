from __future__ import annotations

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
    HarnessRunResult,
    HarnessRunResultEvent,
    HarnessState,
    ModelRecoveryPolicy,
    PluginError,
    RunCleanupError,
    SemanticRunInput,
)
from a13n_harness.plugins import PluginRunExchange, PluginRunNext, PluginRunResponse
from a13n_harness.usage import UsageSnapshot
from pydantic import BaseModel
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.tools import RunContext

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("eager_next", [False, True])
async def test_plugin_points_span_recovery_without_rebinding(eager_next: bool) -> None:
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

        def wrap_run(self, exchange: PluginRunExchange, call_next: PluginRunNext[Any]) -> PluginRunResponse[Any]:
            assert exchange.context.plugins.require(self.plugin_id, Plugin) is self
            log.append(f"wrap:{self.plugin_id}")
            transformed = exchange.with_input(SemanticRunInput(f"{exchange.input.value}|{self.plugin_id}"))
            response = call_next(transformed) if eager_next else None

            async def iterate():
                log.append(f"iterate:{self.plugin_id}")
                try:
                    inner = response if response is not None else call_next(transformed)
                    async for item in inner:
                        if isinstance(item, HarnessRunResult):
                            log.append(f"result:{self.plugin_id}")
                            item = item.replace(output=f"{item.output}|{self.plugin_id}")
                        yield item
                finally:
                    log.append(f"close:{self.plugin_id}")

            return PluginRunResponse(iterate())

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
        entry_log = [*build_log, "bind:outer", "bind:inner", "wrap:outer"]
        if eager_next:
            entry_log.append("wrap:inner")
        assert log == entry_log
        assert contexts == [stream.context, stream.context]
        assert stream.result is None
        assert model_inputs == []
        items = [item async for item in stream]

    iteration_log = (
        ["iterate:outer", "iterate:inner"] if eager_next else ["iterate:outer", "wrap:inner", "iterate:inner"]
    )
    assert log == [
        *entry_log,
        *iteration_log,
        "attempt",
        "model",
        "attempt",
        "model",
        "result:inner",
        "result:outer",
        "close:inner",
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

        def wrap_run(self, exchange: PluginRunExchange, call_next: PluginRunNext[Any]) -> PluginRunResponse[Any]:
            async def iterate():
                nonlocal transferred
                await exchange.context.state.write("test.value", Value(value=1), version="1")
                snapshots.append(await exchange.export_current_state())
                try:
                    async for item in call_next(exchange):
                        if isinstance(item, HarnessRunResult):
                            await exchange.context.state.write("test.value", Value(value=2), version="1")
                            snapshots.append(await exchange.export_current_state())
                            # Trusted middleware may transfer state independently of result history.
                            transferred = HarnessState.new(
                                thread_id=exchange.context.thread_id,
                                agent_context_state=snapshots[0].agent_context_state,
                            )
                            item = item.replace(state=transferred)
                        yield item
                finally:
                    await exchange.context.state.write("test.value", Value(value=3), version="1")
                    if cleanup_fails:
                        raise RuntimeError("checkpoint cleanup failed")

            return PluginRunResponse(iterate())

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
    assert await stream.export_state() == transferred
    assert stream.outcome is not None and stream.outcome.state == transferred
    assert stream.outcome.all_messages() != transferred.message_history
    if cleanup_fails:
        assert stream.result is None
        assert not any(isinstance(item, HarnessRunResultEvent) for item in items)
    else:
        assert isinstance(items[-1], HarnessRunResultEvent)
        assert stream.result == items[-1].result
