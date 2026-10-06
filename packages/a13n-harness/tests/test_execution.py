from __future__ import annotations

import asyncio
import json
import threading
import warnings
from collections.abc import AsyncIterator, Awaitable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any

import pytest
from a13n_harness import (
    AgentDefinition,
    DefinitionError,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessRunResultEvent,
    HarnessState,
    PluginError,
    RunBindings,
    RunError,
    StateError,
    SubagentDefinition,
)
from a13n_harness import AgentSpec as HarnessAgentSpec
from a13n_harness.context import AgentContext
from a13n_harness.events import _RunEventEmitter
from a13n_harness.providers.environment.models import EnvironmentState
from pydantic import BaseModel, ConfigDict, Field, RootModel
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, PartStartEvent
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.output import NativeOutput, PromptedOutput, TextOutput, ToolOutput
from pydantic_ai.tools import DeferredToolRequests, Tool
from pydantic_ai.usage import RunUsage, UsageLimits
from typing_extensions import TypedDict

pytestmark = pytest.mark.anyio


async def _consume_stream(stream: Any) -> list[Any]:
    return [item async for item in stream]


def _turn_model(calls: list[tuple[ModelMessage, ...]]) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        calls.append(tuple(messages))
        turn = sum(isinstance(message, ModelResponse) for message in messages) + 1
        yield f"turn-{turn}"

    return FunctionModel(stream_function=stream)


def _structured_output_model(
    payload: dict[str, Any],
    *,
    schemas: list[dict[str, Any]] | None = None,
) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[DeltaToolCalls]:
        del messages
        output_tool = info.output_tools[0]
        if schemas is not None:
            schemas.append(output_tool.parameters_json_schema)
        yield {
            0: DeltaToolCall(
                name=output_tool.name,
                json_args=json.dumps(payload),
                tool_call_id="output-1",
            )
        }

    return FunctionModel(stream_function=stream)


class _ModelOutput(BaseModel):
    value: int


@dataclass
class _DataclassOutput:
    value: int


class _TypedDictOutput(TypedDict):
    value: int


class _NestedModelOutput(BaseModel):
    value: DeferredToolRequests


@dataclass
class _NestedDataclassOutput:
    value: DeferredToolRequests


class _NestedTypedDictOutput(TypedDict):
    value: DeferredToolRequests


@dataclass
class _GenericDataclassOutput[NestedT]:
    value: NestedT


class _RuntimeModelOutput(BaseModel):
    value: Any


@dataclass
class _RuntimeDataclassOutput:
    value: Any


class _RuntimeRootOutput(RootModel[Any]):
    pass


class _RuntimeExtraOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    value: int


def _leaking_list_output(value: str) -> list[Any]:
    del value
    return [DeferredToolRequests()]


def _leaking_model_output(value: str) -> _RuntimeModelOutput:
    del value
    return _RuntimeModelOutput(value=DeferredToolRequests())


def _leaking_dataclass_output(value: str) -> _RuntimeDataclassOutput:
    del value
    return _RuntimeDataclassOutput(value=DeferredToolRequests())


def _leaking_root_output(value: str) -> _RuntimeRootOutput:
    del value
    return _RuntimeRootOutput(DeferredToolRequests())


def _leaking_extra_output(value: str) -> _RuntimeExtraOutput:
    del value
    return _RuntimeExtraOutput(value=1, hidden=DeferredToolRequests())


def _build(model: FunctionModel):
    return HarnessBuilder().build(
        AgentSpec(name="test-agent"),
        output_type=str,
        model=model,
    )


async def test_omitted_limits_allow_more_than_fifty_requests_for_plain_native_spec() -> None:
    def step() -> None:
        return None

    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        response_count = sum(isinstance(message, ModelResponse) for message in messages)
        if response_count < 51:
            yield {
                0: DeltaToolCall(
                    name="step",
                    json_args="{}",
                    tool_call_id=f"step-{response_count}",
                )
            }
            return
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(tools=[Tool(step)]),),
    )

    result = await executable.run("start")

    assert result.output_or_raise() == "done"
    assert result.usage.requests == 52


async def test_agent_spec_limits_apply_unless_one_run_supplies_an_exact_override() -> None:
    executable = HarnessBuilder().build(
        HarnessAgentSpec(usage_limits=UsageLimits(request_limit=0)),
        output_type=str,
        model=_turn_model([]),
    )

    detached_limits = executable.definition_usage_limits()
    assert detached_limits.request_limit == 0
    detached_limits.request_limit = 99

    limited = await executable.run("blocked")
    overridden = await executable.run(
        "allowed",
        usage_limits=UsageLimits(request_limit=2),
    )

    assert limited.status == "failed"
    assert overridden.output_or_raise() == "turn-1"
    assert executable.definition_usage_limits().request_limit == 0


async def test_stream_is_lazy_and_delivers_one_terminal_result_after_events() -> None:
    calls: list[tuple[ModelMessage, ...]] = []
    executable = _build(_turn_model(calls))

    async with executable.stream("hello", bindings=RunBindings.embedded()) as stream:
        assert calls == []
        assert stream.context.environment.snapshot.mounts == ()
        assert len(stream.context.subagents) == 0

        items = [item async for item in stream]

        assert calls
        assert isinstance(items[-1], HarnessRunResultEvent)
        assert all(isinstance(item, HarnessEvent) for item in items[:-1])
        assert all(item.thread_id == stream.thread_id for item in items)
        assert all(item.run_id == stream.run_id for item in items)
        assert items[-1].result.thread_id == stream.thread_id
        assert items[-1].result.run_id == stream.run_id
        assert [item.sequence for item in items] == list(range(len(items)))
        assert stream.result is items[-1].result

    result = items[-1].result
    assert result.status == "completed"
    assert result.output_or_raise() == "turn-1"
    assert result.state is not None
    assert result.state.message_history == result.all_messages()
    assert len(result.new_messages()) == 2
    assert result.usage.requests == 1


async def test_previous_environment_states_are_host_owned_and_current_states_are_exported() -> None:
    calls: list[tuple[ModelMessage, ...]] = []
    executable = _build(_turn_model(calls))
    previous = HarnessState.new(
        environment_states={
            "workspace": EnvironmentState(
                provider_key="test_provider",
                state_version="state-1",
                state={"target": "previous"},
            )
        }
    )

    async def input_factory(preparation) -> str:
        assert preparation.environment.snapshot.mounts == ()
        assert preparation.environment.snapshot.default_mount is None
        assert preparation.environment.dump_states() == {}
        return "current"

    result = await executable.run(
        input_factory=input_factory,
        bindings=RunBindings.embedded(),
        previous_state=previous,
    )

    assert result.output_or_raise() == "turn-1"
    assert result.state is not None
    assert result.state.environment_states == {}


async def test_enter_and_exit_without_iteration_does_not_start_the_agent() -> None:
    calls: list[tuple[ModelMessage, ...]] = []
    executable = _build(_turn_model(calls))

    async with executable.stream("hello", bindings=RunBindings.embedded()):
        pass

    assert calls == []


async def test_run_consumes_the_canonical_stream_and_state_resumes_a_rebuilt_agent() -> None:
    first_calls: list[tuple[ModelMessage, ...]] = []
    first_executable = _build(_turn_model(first_calls))
    first = await first_executable.run("first", bindings=RunBindings.embedded())
    assert first.output == "turn-1"
    assert first.state is not None
    first_thread_id = first.state.thread_id

    encoded_state = first.state.model_dump_json()
    restored_state = HarnessState.model_validate_json(encoded_state)

    second_calls: list[tuple[ModelMessage, ...]] = []
    rebuilt_executable = _build(_turn_model(second_calls))
    second = await rebuilt_executable.run(
        "second",
        bindings=RunBindings.embedded(),
        previous_state=restored_state,
    )

    assert second.output == "turn-2"
    assert second.all_messages()[: len(first.all_messages())] == first.all_messages()
    assert len(second.new_messages()) == 2
    assert second.state is not None
    assert second.state.message_history == second.all_messages()
    assert first.thread_id == first_thread_id
    assert second.thread_id == first_thread_id
    assert second.state.thread_id == first_thread_id
    assert first.run_id.startswith("run-")
    assert second.run_id.startswith("run-")
    assert second.run_id != first.run_id
    assert first_executable.definition is not rebuilt_executable.definition


@pytest.mark.parametrize("annotation", ["awaitable", "coroutine", "annotated_awaitable"])
async def test_output_functions_may_annotate_their_awaitable_result(annotation: str) -> None:
    output_thread_ids: list[int] = []

    async def transform(value: str) -> str:
        return f"{value}|parsed"

    def awaitable_output(value: str) -> Awaitable[str]:
        output_thread_ids.append(threading.get_ident())
        return transform(value)

    def coroutine_output(value: str) -> Coroutine[Any, Any, str]:
        output_thread_ids.append(threading.get_ident())
        return transform(value)

    def annotated_awaitable_output(value: str) -> Annotated[Awaitable[str], "async-result"]:
        output_thread_ids.append(threading.get_ident())
        return transform(value)

    output_functions = {
        "awaitable": awaitable_output,
        "coroutine": coroutine_output,
        "annotated_awaitable": annotated_awaitable_output,
    }
    output_function = output_functions[annotation]
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message="Could not generate return schema.*")
        executable = HarnessBuilder().build(
            AgentSpec(name="test-agent"),
            output_type=TextOutput(output_function),
            model=_turn_model([]),
        )
        result = await executable.run("hello", bindings=RunBindings.embedded())

    assert isinstance(executable.definition.output_type, TextOutput)
    assert executable.definition.output_type.output_function is output_function
    assert output_thread_ids and all(thread_id != threading.get_ident() for thread_id in output_thread_ids)
    assert result.output_or_raise() == "turn-1|parsed"


async def test_sync_annotated_output_constraints_are_preserved() -> None:
    def constrained_output(value: str) -> Annotated[str, Field(min_length=3)]:
        del value
        return "x"

    executable = HarnessBuilder().build(
        AgentSpec(name="test-agent"),
        output_type=TextOutput(constrained_output),
        model=_turn_model([]),
    )

    with pytest.raises(PluginError) as exc_info:
        await executable.run("hello", bindings=RunBindings.embedded())

    assert exc_info.value.code == "plugin_result_invalid"


@pytest.mark.parametrize(
    "output_function",
    [
        _leaking_list_output,
        _leaking_model_output,
        _leaking_dataclass_output,
        _leaking_root_output,
        _leaking_extra_output,
    ],
)
async def test_nested_deferred_runtime_value_cannot_complete_as_business_output(output_function: Any) -> None:
    executable = HarnessBuilder().build(
        AgentSpec(name="test-agent"),
        output_type=TextOutput(output_function),
        model=_turn_model([]),
    )

    with pytest.raises(PluginError) as exc_info:
        await executable.run("hello", bindings=RunBindings.embedded())

    assert exc_info.value.code == "plugin_result_invalid"


@pytest.mark.parametrize("output_type", [[], ()])
async def test_empty_output_sequence_uses_the_definition_error_boundary(output_type: Any) -> None:
    with pytest.raises(DefinitionError) as exc_info:
        HarnessBuilder().build(
            AgentSpec(),
            output_type=output_type,
            model=_turn_model([]),
        )

    assert exc_info.value.code == "agent_build_failed"
    assert str(exc_info.value) == "Agent construction failed."
    assert isinstance(exc_info.value.__cause__, ValueError)


type _DeferredAlias = DeferredToolRequests


async def _deferred_after_await() -> DeferredToolRequests:
    return DeferredToolRequests()


def _annotated_awaitable_deferred_from_text(
    value: str,
) -> Annotated[Awaitable[DeferredToolRequests], "async-result"]:
    del value
    return _deferred_after_await()


@pytest.mark.parametrize(
    "output_spec",
    [
        DeferredToolRequests,
        _DeferredAlias,
        Annotated[DeferredToolRequests, "reserved"],
        (str, DeferredToolRequests),
        str | DeferredToolRequests,
        list[DeferredToolRequests],
        _NestedModelOutput,
        _NestedDataclassOutput,
        _NestedTypedDictOutput,
        _GenericDataclassOutput[DeferredToolRequests],
        NativeOutput(DeferredToolRequests),
        PromptedOutput(DeferredToolRequests),
        ToolOutput(DeferredToolRequests),
        TextOutput(_annotated_awaitable_deferred_from_text),
    ],
)
async def test_deferred_requests_cannot_be_declared_as_business_output(output_spec: Any) -> None:
    with pytest.raises(DefinitionError) as exc_info:
        AgentDefinition(
            agent=AgentSpec(),
            output_type=output_spec,
            model=_turn_model([]),
        )
    assert exc_info.value.code == "output_contract_reserved"


async def test_agent_spec_output_schema_cannot_override_business_output() -> None:
    with pytest.raises(DefinitionError) as exc_info:
        AgentDefinition(
            agent=AgentSpec(
                output_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            ),
            output_type=str,
            model=_turn_model([]),
        )
    assert exc_info.value.code == "output_contract_conflict"


async def test_missing_build_time_output_contract_is_rejected() -> None:
    with pytest.raises(DefinitionError) as exc_info:
        AgentDefinition(
            agent=AgentSpec(),
            output_type=None,
            model=_turn_model([]),
        )
    assert exc_info.value.code == "output_contract_missing"


@pytest.mark.parametrize(
    ("output_type", "expected"),
    [
        (_ModelOutput, _ModelOutput(value=7)),
        (_DataclassOutput, _DataclassOutput(value=7)),
        (_TypedDictOutput, {"value": 7}),
    ],
)
async def test_code_first_structured_output_types_are_fixed_at_build(
    output_type: Any,
    expected: Any,
) -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=output_type,
        model=_structured_output_model({"value": 7}),
    )

    result = await executable.run("build once", bindings=RunBindings.embedded())

    assert result.output_or_raise() == expected


async def test_agent_spec_object_schema_becomes_native_structured_dict_output() -> None:
    schema = {
        "type": "object",
        "title": "DeclaredOutput",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    observed_schemas: list[dict[str, Any]] = []
    executable = HarnessBuilder().build(
        AgentSpec(output_schema=schema),
        output_type=None,
        model=_structured_output_model({"value": 11}, schemas=observed_schemas),
    )

    expected_schema = json.loads(json.dumps(schema))
    schema["properties"]["value"]["type"] = "string"
    assert executable.definition.agent.output_schema == expected_schema
    executable.definition.agent.output_schema["properties"]["value"]["type"] = "boolean"

    result = await executable.run("schema", bindings=RunBindings.embedded())

    assert result.output_or_raise() == {"value": 11}
    assert observed_schemas == [expected_schema]


async def test_invalid_agent_spec_output_schema_fails_during_build() -> None:
    with pytest.raises(DefinitionError) as exc_info:
        HarnessBuilder().build(
            AgentSpec(output_schema={"type": "array", "items": {"type": "string"}}),
            output_type=None,
            model=_structured_output_model({"value": 1}),
        )

    assert exc_info.value.code == "agent_build_failed"
    assert exc_info.value.__cause__ is not None


async def test_declarative_schema_build_still_supports_native_deferred_suspension() -> None:
    def change(value: int) -> int:
        return value

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[DeltaToolCalls]:
        del messages, info
        yield {
            0: DeltaToolCall(
                name="change",
                json_args=json.dumps({"value": 1}),
                tool_call_id="approval-1",
            )
        }

    executable = HarnessBuilder().build(
        AgentSpec(
            output_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            },
        ),
        output_type=None,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            Capability(
                tools=[Tool(change, requires_approval=True)],
                id="test-tools",
            ),
        ),
    )

    result = await executable.run("suspend", bindings=RunBindings.embedded())

    assert result.status == "suspended"
    assert result.output is None
    assert result.deferred is not None
    assert len(result.deferred.approvals) == 1


async def test_builder_recursively_builds_reusable_authored_subagents() -> None:
    child_definition = AgentDefinition(
        agent=AgentSpec(name="child-agent"),
        output_type=str,
        model=_turn_model([]),
    )
    edge = SubagentDefinition(
        name="researcher",
        description="Research a bounded question.",
        agent=child_definition,
        usage_limits=UsageLimits(request_limit=3),
    )
    executable = HarnessBuilder().build(
        AgentSpec(name="parent-agent"),
        output_type=str,
        model=_turn_model([]),
        subagents=(edge,),
    )

    child = executable.subagents.require("researcher")
    assert child.definition is child_definition
    assert child.declaration is not edge
    assert child.declaration.agent is child_definition
    assert child.executable.definition is child_definition

    assert edge.usage_limits is not None
    edge.usage_limits.request_limit = 99
    assert child.declaration.usage_limits is not None
    assert child.declaration.usage_limits.request_limit == 3

    detached_declaration = child.declaration
    assert detached_declaration.usage_limits is not None
    detached_declaration.usage_limits.request_limit = None
    assert child.declaration.usage_limits is not None
    assert child.declaration.usage_limits.request_limit == 3

    async with executable.stream("parent", bindings=RunBindings.embedded()) as stream:
        assert stream.context.subagents is executable.subagents
        terminal = [item async for item in stream][-1]
        assert isinstance(terminal, HarnessRunResultEvent)

    first_child_result = await child.executable.run("child", bindings=RunBindings.embedded())
    second_child_result = await child.executable.run("child again", bindings=RunBindings.embedded())
    assert first_child_result.output_or_raise() == "turn-1"
    assert second_child_result.output_or_raise() == "turn-1"


async def test_duplicate_subagent_names_fail_before_build() -> None:
    child_definition = AgentDefinition(
        agent=AgentSpec(name="child-agent"),
        output_type=str,
        model=_turn_model([]),
    )
    duplicate = SubagentDefinition(
        name="child",
        description="A child.",
        agent=child_definition,
    )

    with pytest.raises(DefinitionError) as exc_info:
        HarnessBuilder().build(
            AgentSpec(name="parent-agent"),
            output_type=str,
            model=_turn_model([]),
            subagents=(duplicate, duplicate),
        )
    assert exc_info.value.code == "subagent_name_duplicate"


async def test_every_run_gets_a_fresh_context() -> None:
    executable = _build(_turn_model([]))
    contexts = []

    async with executable.stream("one", bindings=RunBindings.embedded()) as first_stream:
        contexts.append(first_stream.context)
        async for _ in first_stream:
            pass

    async with executable.stream("two", bindings=RunBindings.embedded()) as second_stream:
        contexts.append(second_stream.context)
        async for _ in second_stream:
            pass

    assert contexts[0] is not contexts[1]
    assert contexts[0].state is not contexts[1].state
    assert contexts[0].plugins is not contexts[1].plugins
    assert contexts[0].run_id != contexts[1].run_id
    assert contexts[0].thread_id != contexts[1].thread_id


async def test_context_restores_state_owned_thread_identity() -> None:
    executable = _build(_turn_model([]))
    previous = HarnessState.new()

    async with executable.stream(
        "continue",
        bindings=RunBindings.embedded(),
        previous_state=previous,
    ) as stream:
        assert stream.context.thread_id == previous.thread_id
        terminal = [item async for item in stream][-1]

    assert isinstance(terminal, HarnessRunResultEvent)
    assert terminal.result.state is not None
    assert terminal.result.state.thread_id == previous.thread_id


async def test_input_factory_runs_once_after_noop_environment_entry() -> None:
    calls: list[str] = []
    model_calls: list[tuple[ModelMessage, ...]] = []
    executable = _build(_turn_model(model_calls))

    async def input_factory(preparation):
        assert preparation.environment.snapshot.mounts == ()
        calls.append(preparation.run_id)
        return "from factory"

    async with executable.stream(input_factory=input_factory, bindings=RunBindings.embedded()) as stream:
        assert calls == [stream.run_id]
        assert model_calls == []
        async for _ in stream:
            pass

    assert len(calls) == 1
    assert len(model_calls) == 1


async def test_native_cancellation_becomes_a_cancelled_result() -> None:
    started = asyncio.Event()

    async def blocking_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        started.set()
        await asyncio.Event().wait()
        yield "unreachable"

    executable = _build(FunctionModel(stream_function=blocking_stream))

    async with executable.stream("cancel me", bindings=RunBindings.embedded()) as stream:
        next_item = asyncio.create_task(stream.__anext__())
        await started.wait()
        stream.cancel()
        terminal = await asyncio.wait_for(next_item, timeout=2)
        while isinstance(terminal, HarnessEvent):
            terminal = await asyncio.wait_for(stream.__anext__(), timeout=2)

        assert isinstance(terminal, HarnessRunResultEvent)
        assert terminal.result.status == "cancelled"
        assert terminal.result.output is None
        assert stream.result is terminal.result


async def test_prestart_cancellation_never_calls_the_model_or_counts_budget_baseline() -> None:
    calls: list[tuple[ModelMessage, ...]] = []
    executable = _build(_turn_model(calls))
    supplied_usage = RunUsage(requests=7, details={"cached": 2})

    async with executable.stream(
        "cancel before start",
        bindings=RunBindings.embedded(),
        usage=supplied_usage,
    ) as stream:
        stream.cancel()
        started = await stream.__anext__()
        assert isinstance(started, HarnessEvent)
        assert isinstance(started.event, HarnessExtensionEvent)
        assert started.event.payload["type"] == "run_started"
        terminal = await stream.__anext__()

    assert isinstance(terminal, HarnessRunResultEvent)
    assert terminal.result.status == "cancelled"
    assert terminal.result.state is not None
    assert terminal.result.state.message_history == ()
    assert await stream.export_state() == terminal.result.state
    assert terminal.result.usage.requests == 0
    assert terminal.result.usage.details == {}
    assert calls == []


@pytest.mark.parametrize("cancel_before_entry", [False, True])
async def test_prestart_cancellation_preserves_imported_history_and_shutdown_checkpoint(
    cancel_before_entry: bool,
) -> None:
    calls: list[tuple[ModelMessage, ...]] = []
    executable = _build(_turn_model(calls))
    first = await executable.run("first turn")
    assert first.state is not None
    original_state = first.state.model_copy(deep=True)
    calls.clear()
    stream = executable.stream("cancelled turn", previous_state=first.state)
    if cancel_before_entry:
        stream.cancel()
    async with stream:
        if not cancel_before_entry:
            stream.cancel()
        items = [item async for item in stream]

    terminal = items[-1]
    assert isinstance(terminal, HarnessRunResultEvent)
    result = terminal.result
    assert result.status == "cancelled"
    assert result.state is not None
    assert result.state.thread_id == original_state.thread_id
    assert result.all_messages() == original_state.message_history
    assert result.new_messages() == ()
    assert result.state.message_history == original_state.message_history
    assert await stream.export_state() == result.state
    assert first.state == original_state
    assert calls == []

    resumed = await executable.run("next turn", previous_state=result.state)
    assert resumed.output_or_raise() == "turn-2"


async def test_started_stream_closes_the_model_when_the_caller_stops_early() -> None:
    closed = asyncio.Event()

    async def open_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        try:
            yield "partial"
            await asyncio.Event().wait()
        finally:
            closed.set()

    executable = _build(FunctionModel(stream_function=open_stream))

    async with executable.stream("start", bindings=RunBindings.embedded()) as stream:
        while True:
            item = await stream.__anext__()
            assert isinstance(item, HarnessEvent)
            if isinstance(item.event, PartStartEvent):
                break

    await asyncio.wait_for(closed.wait(), timeout=2)


async def test_concurrent_next_is_rejected_without_closing_the_active_stream() -> None:
    started = asyncio.Event()

    async def blocking_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        started.set()
        await asyncio.Event().wait()
        yield "unreachable"

    executable = _build(FunctionModel(stream_function=blocking_stream))

    async with executable.stream("hello", bindings=RunBindings.embedded()) as stream:
        for expected_type in ("run_started", "model_request_started"):
            lifecycle = await stream.__anext__()
            assert isinstance(lifecycle, HarnessEvent)
            assert isinstance(lifecycle.event, HarnessExtensionEvent)
            assert lifecycle.event.payload["type"] == expected_type

        from a13n_harness.events import InputTextEvent

        observed_input = await stream.__anext__()
        assert isinstance(observed_input, HarnessEvent) and isinstance(observed_input.event, InputTextEvent)
        assert observed_input.event.source == "user"
        projected_context = await stream.__anext__()
        assert isinstance(projected_context, HarnessEvent) and isinstance(projected_context.event, InputTextEvent)
        assert projected_context.event.source == "context"
        next_entered = asyncio.Event()

        async def read_next():
            next_entered.set()
            return await stream.__anext__()

        active_next = asyncio.create_task(read_next())
        await next_entered.wait()
        await started.wait()
        with pytest.raises(RunError) as exc_info:
            await stream.__anext__()
        assert exc_info.value.code == "run_stream_concurrent_next"

        stream.cancel()
        terminal = await asyncio.wait_for(active_next, timeout=2)
        while isinstance(terminal, HarnessEvent):
            terminal = await asyncio.wait_for(stream.__anext__(), timeout=2)
        assert isinstance(terminal, HarnessRunResultEvent)
        assert terminal.result.status == "cancelled"


async def test_stream_steer_delivers_native_asap_input() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[tuple[ModelMessage, ...]] = []

    async def steering_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        calls.append(tuple(messages))
        if len(calls) == 1:
            started.set()
            await release.wait()
            yield "first response"
        else:
            yield "done"

    executable = _build(FunctionModel(stream_function=steering_stream))
    async with executable.stream("initial", bindings=RunBindings.embedded()) as stream:
        consumer = asyncio.create_task(_consume_stream(stream))
        await started.wait()
        usage_id = stream.context.usage_snapshot.usage_id
        enqueue_id = await stream.steer("additional user context")
        release.set()
        items = await asyncio.wait_for(consumer, timeout=2)

        assert enqueue_id
        assert len(calls) == 2
        assert "additional user context" in str(calls[1])
        terminal = items[-1]
        assert isinstance(terminal, HarnessRunResultEvent)
        assert terminal.result.output_or_raise() == "done"
        assert stream.context.usage_snapshot.usage_id == usage_id
        assert terminal.result.usage.requests == 2
        with pytest.raises(RunError) as terminal_error:
            await stream.steer("too late")
        assert terminal_error.value.code == "run_not_active"


async def test_harness_lifecycle_notice_steers_active_run_and_emits_public_event() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[tuple[ModelMessage, ...]] = []

    async def steering_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        calls.append(tuple(messages))
        if len(calls) == 1:
            started.set()
            await release.wait()
            yield "first response"
        else:
            yield "collected"

    executable = _build(FunctionModel(stream_function=steering_stream))
    async with executable.stream("initial", bindings=RunBindings.embedded()) as stream:
        consumer = asyncio.create_task(_consume_stream(stream))
        await started.wait()
        enqueue_id = await stream.context._steering.notify(
            "Background subagent subagent-1 has finished. Call wait_subagent.",
            source="async_subagent",
            references=("subagent-1",),
        )
        release.set()
        items = await asyncio.wait_for(consumer, timeout=2)

    assert enqueue_id
    assert len(calls) == 2
    assert "wait_subagent" in str(calls[1])
    from a13n_harness.content import request_input_content
    from a13n_harness.events import InputTextEvent
    from pydantic_ai.messages import EnqueuedMessagesEvent, ModelRequest

    delivered = next(
        item for item in items if isinstance(item, HarnessEvent) and isinstance(item.event, EnqueuedMessagesEvent)
    )
    content = [
        content
        for message in delivered.event.messages
        if isinstance(message, ModelRequest)
        for content in request_input_content(message)
    ]
    assert content[0].metadata.display is False
    assert content[0].metadata.source_id == "a13n.async_subagent"
    assert content[0].value == "Background subagent subagent-1 has finished. Call wait_subagent."
    observed = [
        item.event
        for item in items
        if isinstance(item, HarnessEvent)
        and isinstance(item.event, InputTextEvent)
        and item.event.source == "async_subagent"
    ]
    assert len(observed) == 1
    assert observed[0].content == content[0].value
    assert observed[0].input_id == enqueue_id
    notifications = [
        item.event
        for item in items
        if isinstance(item, HarnessEvent)
        and isinstance(item.event, HarnessExtensionEvent)
        and item.event.payload.get("type") == "steering_input_enqueued"
    ]
    assert len(notifications) == 1
    assert notifications[0].kind == "lifecycle"
    assert notifications[0].payload == {
        "type": "steering_input_enqueued",
        "enqueue_id": enqueue_id,
        "source": "async_subagent",
        "references": ["subagent-1"],
    }


async def test_stream_steer_requires_an_active_native_run() -> None:
    executable = _build(_turn_model([]))
    stream = executable.stream("initial", bindings=RunBindings.embedded())

    with pytest.raises(RunError) as before_entry:
        await stream.steer("early")
    assert before_entry.value.code == "run_not_active"

    async with stream:
        with pytest.raises(RunError) as before_iteration:
            await stream.steer("still early")
        assert before_iteration.value.code == "run_not_active"


async def test_usage_limit_has_a_specific_safe_failure() -> None:
    executable = _build(_turn_model([]))

    result = await executable.run(
        "hello",
        bindings=RunBindings.embedded(),
        usage_limits=UsageLimits(request_limit=0),
    )

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "usage_limit_exceeded"
    assert result.failure.message == "Run usage limit exceeded."
    assert result.failure.retry_hint == "dependency_change"


@pytest.mark.parametrize(
    "error",
    [UnexpectedModelBehavior("private-provider-body"), ModelHTTPError(429, "test-model", "private-provider-body")],
)
async def test_recognized_pydantic_run_failure_becomes_a_failed_result(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    async def failing_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        raise error
        yield "unreachable"

    executable = _build(FunctionModel(stream_function=failing_stream))
    result = await executable.run("fail", bindings=RunBindings.embedded())

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "agent_run_failed"
    assert result.failure.message == "Agent execution failed."
    assert result.failure.details["exception_type"] == type(error).__name__
    if isinstance(error, ModelHTTPError):
        assert result.failure.details["status_code"] == 429
    assert "private-provider-body" not in str(result.failure)
    assert "private-provider-body" not in caplog.text
    assert result.thread_id in caplog.text
    assert result.run_id in caplog.text
    assert "failing_stream" in caplog.text
    assert type(error).__name__ in caplog.text


async def test_closed_stream_exports_its_validated_result_state() -> None:
    executable = _build(_turn_model([]))
    async with executable.stream("hello", bindings=RunBindings.embedded()) as stream:
        items = await _consume_stream(stream)
    terminal = items[-1]
    assert isinstance(terminal, HarnessRunResultEvent)
    assert await stream.export_state() == terminal.result.state


async def test_closed_stream_export_failure_does_not_mask_consumer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_export(self, message_history):
        del self, message_history
        raise ValueError("invalid capability state")

    executable = _build(_turn_model([]))
    stream = executable.stream("hello", bindings=RunBindings.embedded())
    with pytest.raises(RuntimeError, match="consumer failed"):
        async with stream:
            monkeypatch.setattr(AgentContext, "export_state", fail_export)
            raise RuntimeError("consumer failed")
    with pytest.raises(StateError) as error:
        await stream.export_state()
    assert error.value.code == "run_state_unavailable"
    assert isinstance(error.value.__cause__, ValueError)


async def test_event_consumer_stop_wakes_a_blocked_environment_change_producer() -> None:
    emitter = _RunEventEmitter("thread-1", "run-1", capacity=1)
    event = HarnessExtensionEvent(kind="context", payload={"type": "environment_changed"})
    emitter.start_consuming()
    await emitter.emit(event)
    blocked = asyncio.create_task(emitter.emit(event))
    await asyncio.sleep(0)
    assert not blocked.done()

    emitter.stop_consuming()

    with pytest.raises(RunError) as stopped:
        await asyncio.wait_for(blocked, timeout=0.2)
    assert stopped.value.code == "event_consumer_stopped"
    emitter.close()


async def test_binding_failure_during_cancellation_preserves_empty_history_state() -> None:
    from pydantic_ai import RunContext
    from pydantic_ai.capabilities import AbstractCapability

    class SavedValue(BaseModel):
        value: int

    class FailingBinding(AbstractCapability[AgentContext]):
        async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
            await ctx.deps.state.write("test.saved", SavedValue(value=7), version="1")
            stream.cancel()
            raise RuntimeError("binding failed during cancellation")

    calls: list[tuple[ModelMessage, ...]] = []
    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=_turn_model(calls), capabilities=(FailingBinding(),)
    )
    stream = executable.stream("start")
    async with stream:
        items = [item async for item in stream]
    terminal = items[-1]
    assert isinstance(terminal, HarnessRunResultEvent)
    assert terminal.result.status == "cancelled"
    assert terminal.result.state is not None
    assert terminal.result.state.message_history == ()
    assert terminal.result.state.agent_context_state.entries["test.saved"].data == {"value": 7}
    assert await stream.export_state() == terminal.result.state
    assert calls == []


async def test_stream_steer_records_host_input_id_in_exported_history() -> None:
    from a13n_harness.capabilities.steering import steering_input_ids

    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[tuple[ModelMessage, ...]] = []

    async def steering_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        calls.append(tuple(messages))
        if len(calls) == 1:
            started.set()
            await release.wait()
            yield "first response"
        else:
            yield "done"

    executable = _build(FunctionModel(stream_function=steering_stream))
    async with executable.stream("initial", bindings=RunBindings.embedded()) as stream:
        consumer = asyncio.create_task(_consume_stream(stream))
        await started.wait()
        assert steering_input_ids((await stream.export_state()).message_history) == ()
        await stream.steer("host context", input_id="inb_1")
        release.set()
        items = await asyncio.wait_for(consumer, timeout=2)

    terminal = items[-1]
    assert isinstance(terminal, HarnessRunResultEvent)
    assert terminal.result.state is not None
    assert steering_input_ids(terminal.result.state.message_history) == ("inb_1",)
    assert steering_input_ids(calls[1]) == ("inb_1",)
