from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, cast

import pytest
from a13n_harness import (
    AbstractHarnessPlugin,
    AgentContext,
    BoundEnvironment,
    EnvironmentError,
    EnvironmentRunBinding,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessRunResult,
    PluginError,
    PluginOrdering,
    PluginRunExchange,
    PluginRunNext,
    PluginRunResponse,
    RunBindings,
    RunCleanupError,
    RunError,
    SemanticRunInput,
    create_noop_environment_run_binding,
)
from pydantic import ValidationError
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RunUsage

pytestmark = pytest.mark.anyio


class RecordingCapability(AbstractCapability[AgentContext]):
    def __init__(self, log: list[str]) -> None:
        self.id = "recording-capability"
        self.log = log

    async def before_run(self, ctx: RunContext[AgentContext]) -> None:
        assert ctx.deps.plugins.require("inner", RecordingPlugin).run_bound is True
        self.log.append("capability")


class RecordingPlugin(AbstractHarnessPlugin):
    def __init__(
        self,
        plugin_id: str,
        log: list[str],
        *,
        ordering: PluginOrdering | None = None,
        contribute_capability: bool = False,
        run_bound: bool = False,
    ) -> None:
        self._plugin_id = plugin_id
        self.log = log
        self.ordering = ordering or PluginOrdering()
        self.contribute_capability = contribute_capability
        self.run_bound = run_bound

    @property
    def plugin_id(self) -> str:
        return self._plugin_id

    def get_ordering(self) -> PluginOrdering:
        return self.ordering

    def for_agent(self) -> RecordingPlugin:
        self.log.append(f"agent:{self.plugin_id}")
        return self

    async def for_run(self, context: AgentContext) -> RecordingPlugin:
        self.log.append(f"run:{self.plugin_id}")
        with pytest.raises(PluginError, match="not available"):
            _ = context.plugins.ordered
        return RecordingPlugin(
            self.plugin_id,
            self.log,
            ordering=self.ordering,
            contribute_capability=self.contribute_capability,
            run_bound=True,
        )

    def get_capabilities(self):
        if self.contribute_capability:
            return (RecordingCapability(self.log),)
        return ()

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            self.log.append(f"enter:{self.plugin_id}")
            try:
                async for item in call_next(exchange):
                    if isinstance(item, HarnessRunResult) and item.status == "completed":
                        item = item.replace(output=f"{item.output}|{self.plugin_id}")
                    yield item
            finally:
                self.log.append(f"exit:{self.plugin_id}")

        return PluginRunResponse(iterate())


def _model(log: list[str]) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        log.append("model")
        yield "output"

    return FunctionModel(stream_function=stream)


async def test_plugins_bind_order_wrap_and_contribute_capabilities() -> None:
    log: list[str] = []
    inner = RecordingPlugin("inner", log, contribute_capability=True)
    outer = RecordingPlugin("outer", log, ordering=PluginOrdering(wraps=("inner",)))
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_model(log),
        plugins=(inner, outer),
    )

    result = await executable.run("hello", bindings=RunBindings.local())

    assert result.output == "output|inner|outer"
    assert log == [
        "agent:outer",
        "agent:inner",
        "run:outer",
        "run:inner",
        "enter:outer",
        "enter:inner",
        "capability",
        "model",
        "exit:inner",
        "exit:outer",
    ]


class ShortCircuitPlugin(AbstractHarnessPlugin):
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    @property
    def plugin_id(self) -> str:
        return "short-circuit"

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        del call_next

        async def iterate():
            self.calls.append("short-circuit")
            yield HarnessRunResult(
                thread_id=exchange.context.thread_id,
                run_id=exchange.context.run_id,
                status="completed",
                output="cached",
                state=await exchange.export_current_state(),
                usage=RunUsage(),
            )

        return PluginRunResponse(iterate())


async def test_plugin_can_short_circuit_without_starting_pydantic() -> None:
    calls: list[str] = []
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_model(calls),
        plugins=(ShortCircuitPlugin(calls),),
    )

    result = await executable.run("hello", bindings=RunBindings.local())

    assert result.output == "cached"
    assert calls == ["short-circuit"]
    assert result.all_messages() == ()


class CleanupFailurePlugin(AbstractHarnessPlugin):
    @property
    def plugin_id(self) -> str:
        return "cleanup-failure"

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            try:
                async for item in call_next(exchange):
                    yield item
            finally:
                raise RuntimeError("cleanup failed")

        return PluginRunResponse(iterate())


async def test_cleanup_failure_withholds_terminal_delivery_and_retains_outcome() -> None:
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_model([]),
        plugins=(CleanupFailurePlugin(),),
    )

    with pytest.raises(RunCleanupError) as exc_info:
        await executable.run("hello", bindings=RunBindings.local())

    assert exc_info.value.outcome is not None
    assert exc_info.value.outcome.status == "completed"
    assert exc_info.value.outcome.output == "output"
    assert len(exc_info.value.causes) == 1
    assert isinstance(exc_info.value.causes[0], RuntimeError)


class InvalidOutputPlugin(AbstractHarnessPlugin):
    @property
    def plugin_id(self) -> str:
        return "invalid-output"

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            async for item in call_next(exchange):
                if isinstance(item, HarnessRunResult):
                    yield item.replace(output=123)
                else:
                    yield item

        return PluginRunResponse(iterate())


class RaiseAfterResultPlugin(AbstractHarnessPlugin):
    @property
    def plugin_id(self) -> str:
        return "raise-after-result"

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            async for item in call_next(exchange):
                if isinstance(item, HarnessRunResult):
                    raise RuntimeError("outer plugin failed")
                yield item

        return PluginRunResponse(iterate())


@pytest.mark.parametrize("plugin", [InvalidOutputPlugin(), RaiseAfterResultPlugin()])
async def test_invalid_or_failed_outer_result_retains_the_last_valid_inner_outcome(
    plugin: AbstractHarnessPlugin,
) -> None:
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_model([]),
        plugins=(plugin,),
    )

    with pytest.raises(RunCleanupError) as exc_info:
        await executable.run("hello", bindings=RunBindings.local())

    assert exc_info.value.outcome is not None
    assert exc_info.value.outcome.status == "completed"
    assert exc_info.value.outcome.output == "output"
    assert len(exc_info.value.causes) == 1


class ReplaceResultPlugin(AbstractHarnessPlugin):
    @property
    def plugin_id(self) -> str:
        return "replace-result"

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            async for item in call_next(exchange):
                if isinstance(item, HarnessRunResult):
                    item = item.replace(output=f"{item.output}|inner")
                yield item

        return PluginRunResponse(iterate())


async def test_outer_failure_retains_the_valid_replacement_from_the_inner_plugin_boundary() -> None:
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_model([]),
        plugins=(RaiseAfterResultPlugin(), ReplaceResultPlugin()),
    )

    with pytest.raises(RunCleanupError) as exc_info:
        await executable.run("hello", bindings=RunBindings.local())

    assert exc_info.value.outcome is not None
    assert exc_info.value.outcome.output == "output|inner"


class EventTransformPlugin(AbstractHarnessPlugin):
    def __init__(self, *, invalid: bool = False, foreign_run: bool = False) -> None:
        self.invalid = invalid
        self.foreign_run = foreign_run

    @property
    def plugin_id(self) -> str:
        return "event-transform"

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            async for item in call_next(exchange):
                if isinstance(item, HarnessEvent):
                    yield replace(
                        item,
                        run_id="forged-child" if self.foreign_run else item.run_id,
                        sequence=10_000 - item.sequence,
                        event=cast(Any, object()) if self.invalid else item.event,
                    )
                else:
                    yield item

        return PluginRunResponse(iterate())


async def test_plugin_event_sequences_are_reallocated_at_the_public_boundary() -> None:
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_model([]),
        plugins=(EventTransformPlugin(),),
    )

    async with executable.stream("hello", bindings=RunBindings.local()) as stream:
        items = [item async for item in stream]

    assert len(items) > 1
    assert [item.sequence for item in items] == list(range(len(items)))


async def test_plugin_cannot_emit_a_malformed_pydantic_event() -> None:
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_model([]),
        plugins=(EventTransformPlugin(invalid=True),),
    )

    with pytest.raises(PluginError) as exc_info:
        await executable.run("hello", bindings=RunBindings.local())

    assert exc_info.value.code == "plugin_event_invalid"


async def test_plugin_cannot_forge_an_unregistered_child_event() -> None:
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_model([]),
        plugins=(EventTransformPlugin(foreign_run=True),),
    )

    with pytest.raises(PluginError) as exc_info:
        await executable.run("hello", bindings=RunBindings.local())

    assert exc_info.value.code == "plugin_event_run_mismatch"


class EmitDuringBindingPlugin(AbstractHarnessPlugin):
    @property
    def plugin_id(self) -> str:
        return "emit-during-binding"

    async def for_run(self, context: AgentContext) -> EmitDuringBindingPlugin:
        for index in range(65):
            await context.events.emit(HarnessExtensionEvent(kind="diagnostic", payload={"index": index}))
        return self


async def test_pre_start_event_overflow_fails_without_waiting_for_a_consumer() -> None:
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_model([]),
        plugins=(EmitDuringBindingPlugin(),),
    )

    with pytest.raises(RunError) as exc_info:
        await asyncio.wait_for(
            executable.run("hello", bindings=RunBindings.local()),
            timeout=1,
        )

    assert exc_info.value.code == "event_buffer_full"


def test_extension_events_redact_sensitive_content_and_reject_non_finite_numbers() -> None:
    event = HarnessExtensionEvent(
        kind="diagnostic",
        payload={
            "token": "plain-secret",
            "message": "request used Bearer abc.def",
            "nested": {"credential": "raw-value"},
            "input_tokens": 42,
        },
    )

    assert event.payload == {
        "token": "[REDACTED]",
        "message": "request used Bearer [REDACTED]",
        "nested": {"credential": "[REDACTED]"},
        "input_tokens": 42,
    }
    with pytest.raises(ValidationError):
        HarnessExtensionEvent(kind="diagnostic", payload={"value": float("inf")})


class ExchangeReplacementPlugin(AbstractHarnessPlugin):
    def __init__(self, replacement: str) -> None:
        self.replacement = replacement

    @property
    def plugin_id(self) -> str:
        return f"replace-{self.replacement}"

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        if self.replacement == "context":
            exchange = replace(exchange, context=cast(AgentContext, object()))
        else:
            exchange = replace(
                exchange,
                input=SemanticRunInput(value=cast(Any, b"invalid")),
            )
        return call_next(exchange)


@pytest.mark.parametrize("replacement", ["context", "input"])
async def test_plugin_cannot_replace_trusted_context_or_bypass_input_normalization(replacement: str) -> None:
    model_calls: list[str] = []
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_model(model_calls),
        plugins=(ExchangeReplacementPlugin(replacement),),
    )

    with pytest.raises(PluginError) as exc_info:
        await executable.run("hello", bindings=RunBindings.local())

    assert exc_info.value.code == f"plugin_{replacement}_{'replaced' if replacement == 'context' else 'invalid'}"
    assert model_calls == []


class CleanupTrackingPlugin(AbstractHarnessPlugin):
    def __init__(
        self,
        plugin_id: str,
        log: list[str],
        *,
        ordering: PluginOrdering | None = None,
        started: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
    ) -> None:
        self._plugin_id = plugin_id
        self.log = log
        self.ordering = ordering or PluginOrdering()
        self.started = started
        self.release = release

    @property
    def plugin_id(self) -> str:
        return self._plugin_id

    def get_ordering(self) -> PluginOrdering:
        return self.ordering

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            try:
                async for item in call_next(exchange):
                    yield item
            finally:
                self.log.append(f"start:{self.plugin_id}")
                if self.started is not None and self.release is not None:
                    self.started.set()
                    await self.release.wait()
                self.log.append(f"done:{self.plugin_id}")

        return PluginRunResponse(iterate())


class TaskAffineCleanupPlugin(AbstractHarnessPlugin):
    @property
    def plugin_id(self) -> str:
        return "task-affine-cleanup"

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            owner_task = asyncio.current_task()
            try:
                async for item in call_next(exchange):
                    yield item
            finally:
                assert asyncio.current_task() is owner_task

        return PluginRunResponse(iterate())


async def test_normal_cleanup_stays_in_the_task_that_entered_the_plugin_iterator() -> None:
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_model([]),
        plugins=(TaskAffineCleanupPlugin(),),
    )

    async with executable.stream("hello", bindings=RunBindings.local()) as stream:
        response = cast(Any, stream)._response
        first = await stream.__anext__()
        assert isinstance(first, HarnessEvent)

    assert response._item_validator is None


class TaskAffineEnvironment(EnvironmentRunBinding):
    def __init__(self) -> None:
        self.delegate = create_noop_environment_run_binding()
        self.closed = asyncio.Event()

    @property
    def controller(self):
        return self.delegate.controller

    @property
    def topology_limits(self):
        return self.delegate.topology_limits

    @property
    def state_limits(self):
        return self.delegate.state_limits

    @asynccontextmanager
    async def bind(self, *, run_id: str, instance) -> AsyncGenerator[BoundEnvironment]:
        owner_task = asyncio.current_task()
        async with self.delegate.bind(run_id=run_id, instance=instance) as environment:
            try:
                yield environment
            finally:
                assert asyncio.current_task() is owner_task
                self.closed.set()


async def test_terminal_cleanup_exits_environment_scope_in_its_entering_task() -> None:
    environment = TaskAffineEnvironment()
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_model([]),
    )

    result = await executable.run("hello", bindings=RunBindings.local(environment=environment))

    assert result.output_or_raise() == "output"
    assert environment.closed.is_set()


class FailingLifecycleEnvironment(TaskAffineEnvironment):
    def __init__(self) -> None:
        super().__init__()
        self.fail = asyncio.Event()

    @asynccontextmanager
    async def bind(self, *, run_id: str, instance) -> AsyncGenerator[BoundEnvironment]:
        async with self.delegate.bind(run_id=run_id, instance=instance) as environment:
            async with asyncio.TaskGroup() as tasks:

                async def fail_lifecycle() -> None:
                    await self.fail.wait()
                    raise RuntimeError("environment lifecycle failed")

                tasks.create_task(fail_lifecycle())
                yield environment


async def test_environment_owner_failure_interrupts_the_logical_run() -> None:
    environment = FailingLifecycleEnvironment()
    model_started = asyncio.Event()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        model_started.set()
        await asyncio.Event().wait()
        yield "unreachable"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )
    run_task = asyncio.create_task(executable.run("hello", bindings=RunBindings.local(environment=environment)))
    await model_started.wait()
    environment.fail.set()

    with pytest.raises(BaseExceptionGroup) as exc_info:
        await asyncio.wait_for(run_task, timeout=2)

    assert exc_info.value.subgroup(RuntimeError) is not None
    assert "environment lifecycle failed" in repr(exc_info.value)


async def test_model_stream_stays_in_one_owner_task() -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        owner_task = asyncio.current_task()
        try:
            yield "output"
        finally:
            assert asyncio.current_task() is owner_task

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    result = await executable.run("hello", bindings=RunBindings.local())

    assert result.output_or_raise() == "output"


class CountingCloseResponse(PluginRunResponse):
    def __init__(self, iterator: AsyncIterator) -> None:
        super().__init__(iterator)
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1
        await super().aclose()


class CountingClosePlugin(AbstractHarnessPlugin):
    def __init__(self) -> None:
        self.response: CountingCloseResponse | None = None

    @property
    def plugin_id(self) -> str:
        return "counting-close"

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            async for item in call_next(exchange):
                yield item

        self.response = CountingCloseResponse(iterate())
        return self.response


async def test_harness_closes_each_registered_plugin_response_once() -> None:
    plugin = CountingClosePlugin()
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_model([]),
        plugins=(plugin,),
    )

    await executable.run("hello", bindings=RunBindings.local())

    assert plugin.response is not None
    assert plugin.response.close_calls == 1


class SuppressingCancellationPlugin(AbstractHarnessPlugin):
    def __init__(self, cleanup_started: asyncio.Event) -> None:
        self.cleanup_started = cleanup_started

    @property
    def plugin_id(self) -> str:
        return "suppressing-cancellation"

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            try:
                async for item in call_next(exchange):
                    yield item
            finally:
                self.cleanup_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    pass

        return PluginRunResponse(iterate())


async def test_cleanup_cannot_suppress_external_cancellation() -> None:
    cleanup_started = asyncio.Event()
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_model([]),
        plugins=(SuppressingCancellationPlugin(cleanup_started),),
    )
    stream = executable.stream("hello", bindings=RunBindings.local())
    await stream.__aenter__()
    first = await stream.__anext__()
    assert isinstance(first, HarnessEvent)

    close_task = asyncio.create_task(stream.__aexit__(None, None, None))
    await cleanup_started.wait()
    close_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await close_task


async def test_cancellation_during_terminal_pump_cleanup_stays_primary() -> None:
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    plugin = CleanupTrackingPlugin(
        "terminal-cleanup",
        [],
        started=cleanup_started,
        release=release_cleanup,
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_model([]),
        plugins=(plugin,),
    )

    run_task = asyncio.create_task(executable.run("hello", bindings=RunBindings.local()))
    await cleanup_started.wait()
    run_task.cancel()
    release_cleanup.set()

    with pytest.raises(asyncio.CancelledError):
        await run_task


async def test_early_close_installs_topology_fence_before_plugin_cleanup() -> None:
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    plugin = CleanupTrackingPlugin(
        "early-close-cleanup",
        [],
        started=cleanup_started,
        release=release_cleanup,
    )
    environment = create_noop_environment_run_binding(
        topology_limits=EnvironmentTopologyLimits(max_bindings=1, max_committed_changes=1)
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_model([]),
        plugins=(plugin,),
    )
    stream = executable.stream("hello", bindings=RunBindings.local(environment=environment))
    await stream.__aenter__()
    first = await stream.__anext__()
    assert isinstance(first, HarnessEvent)

    close_task = asyncio.create_task(stream.__aexit__(None, None, None))
    await cleanup_started.wait()
    with pytest.raises(EnvironmentError) as exc_info:
        await environment.controller.apply(
            EnvironmentTopologyRequest(topology_version=1, bindings=(), default_binding_id=None)
        )
    assert exc_info.value.code == "run_not_active"
    release_cleanup.set()
    await close_task


class BlockingCloseResponse(PluginRunResponse):
    def __init__(self, iterator: AsyncIterator, close_started: asyncio.Event) -> None:
        super().__init__(iterator)
        self.close_started = close_started

    async def aclose(self) -> None:
        self.close_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await super().aclose()


class EndAfterTwoEventsPlugin(AbstractHarnessPlugin):
    def __init__(self, close_started: asyncio.Event) -> None:
        self.close_started = close_started

    @property
    def plugin_id(self) -> str:
        return "end-after-two-events"

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            event_count = 0
            async for item in call_next(exchange):
                if isinstance(item, HarnessEvent):
                    yield item
                    event_count += 1
                    if event_count == 2:
                        return

        return BlockingCloseResponse(iterate(), self.close_started)


async def test_internal_pump_cancellation_cannot_publish_into_an_unconsumed_full_queue() -> None:
    close_started = asyncio.Event()
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_model([]),
        plugins=(EndAfterTwoEventsPlugin(close_started),),
    )
    stream = executable.stream("hello", bindings=RunBindings.local())
    await stream.__aenter__()
    first = await stream.__anext__()
    assert isinstance(first, HarnessEvent)
    await asyncio.wait_for(close_started.wait(), timeout=2)

    await asyncio.wait_for(stream.__aexit__(None, None, None), timeout=2)


class FailingTrackingEnvironment(EnvironmentRunBinding):
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.delegate = create_noop_environment_run_binding()

    @property
    def controller(self):
        return self.delegate.controller

    @property
    def topology_limits(self):
        return self.delegate.topology_limits

    @property
    def state_limits(self):
        return self.delegate.state_limits

    @asynccontextmanager
    async def bind(self, *, run_id: str, instance) -> AsyncGenerator[BoundEnvironment]:
        async with self.delegate.bind(run_id=run_id, instance=instance) as environment:
            try:
                yield environment
            finally:
                self.log.append("environment")
                raise RuntimeError("environment cleanup failed")


async def test_repeated_external_cancellation_attempts_remaining_cleanup_and_stays_primary() -> None:
    log: list[str] = []
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    inner = CleanupTrackingPlugin(
        "cleanup-inner",
        log,
        started=cleanup_started,
        release=release_cleanup,
    )
    outer = CleanupTrackingPlugin(
        "cleanup-outer",
        log,
        ordering=PluginOrdering(wraps=("cleanup-inner",)),
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_model([]),
        plugins=(inner, outer),
    )
    stream = executable.stream(
        "hello",
        bindings=RunBindings.local(environment=FailingTrackingEnvironment(log)),
    )
    await stream.__aenter__()
    first = await stream.__anext__()
    assert isinstance(first, HarnessEvent)

    close_task = asyncio.create_task(stream.__aexit__(None, None, None))
    await cleanup_started.wait()
    close_task.cancel()
    release_cleanup.set()

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await close_task

    assert log == [
        "start:cleanup-inner",
        "start:cleanup-outer",
        "done:cleanup-outer",
        "environment",
    ]
    assert any("environment cleanup failed" in note for note in (exc_info.value.__notes__ or []))


def test_plugin_ordering_rejects_unknown_references_and_cycles() -> None:
    model = _model([])
    with pytest.raises(PluginError, match="unknown plugin"):
        HarnessBuilder().build_code(
            AgentSpec(model="logical:test"),
            output_type=str,
            model=model,
            plugins=(RecordingPlugin("one", [], ordering=PluginOrdering(wraps=("missing",))),),
        )

    with pytest.raises(PluginError, match="cycle"):
        HarnessBuilder().build_code(
            AgentSpec(model="logical:test"),
            output_type=str,
            model=model,
            plugins=(
                RecordingPlugin("one", [], ordering=PluginOrdering(wraps=("two",))),
                RecordingPlugin("two", [], ordering=PluginOrdering(wraps=("one",))),
            ),
        )
