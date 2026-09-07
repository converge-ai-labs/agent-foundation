from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
from itertools import pairwise
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_harness import (
    AbstractHarnessPlugin,
    AgentContext,
    HarnessBuilder,
    RunBindings,
)
from a13n_harness.errors import DefinitionError
from a13n_harness.model_context import (
    AbstractModelContextCapability,
    ModelContextBlock,
    ModelContextCoordinatorCapability,
    ModelContextInputOrigin,
    ModelContextNext,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextProjectionRequest,
    ModelContextRequestKind,
    _commit_projection,
    _remove_owned_overlays,
    _validate_projection,
    user_prompt_content,
)
from pydantic_ai import RunContext
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextContent,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.tools import Tool
from pydantic_ai.usage import RunUsage

pytestmark = pytest.mark.anyio


class _ProjectionDeps:
    def __init__(
        self,
        deferred_resume: Any = None,
        projection: ModelContextProjection | None = None,
    ) -> None:
        self.deferred_resume = deferred_resume
        self.model_context = None
        self.projection = projection or ModelContextProjection()
        self.projection_calls = 0

    async def project_model_context(
        self,
        request: ModelContextProjectionRequest,
    ) -> ModelContextProjection:
        del request
        self.projection_calls += 1
        return self.projection


class _ProjectionCapability(AbstractModelContextCapability):
    id = "test.projection-capability"

    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def wrap_model_context(
        self,
        ctx: RunContext[AgentContext],
        request: ModelContextProjectionRequest,
        handler: ModelContextNext,
    ) -> ModelContextProjection:
        del ctx
        self._calls.append("capability:before")
        projection = await handler(request)
        self._calls.append("capability:after")
        return ModelContextProjection(
            blocks=(
                *projection.blocks,
                ModelContextBlock(
                    source_id=self.id,
                    placement=ModelContextPlacement.REQUEST_EPILOGUE,
                    content="plugin context",
                ),
            )
        )


class _SequencedProjectionCapability(AbstractModelContextCapability):
    id = "test.sequenced-projection-capability"

    def __init__(self, requests: list[ModelContextProjectionRequest]) -> None:
        self._requests = requests

    def get_instructions(self) -> str:
        return "Keep injected model context append-only."

    async def wrap_model_context(
        self,
        ctx: RunContext[AgentContext],
        request: ModelContextProjectionRequest,
        handler: ModelContextNext,
    ) -> ModelContextProjection:
        del ctx
        projection = await handler(request)
        self._requests.append(request)
        return ModelContextProjection(
            blocks=(
                *projection.blocks,
                ModelContextBlock(
                    source_id=self.id,
                    placement=ModelContextPlacement.REQUEST_EPILOGUE,
                    content=f"capability context {len(self._requests)}",
                ),
            )
        )


class _ProjectionPlugin(AbstractHarnessPlugin):
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    @property
    def plugin_id(self) -> str:
        return "projection-plugin"

    def get_capabilities(self):
        return (_ProjectionCapability(self._calls),)


class _HostProjection:
    def __init__(self, calls: list[str], *, short_circuit: bool = False) -> None:
        self._calls = calls
        self._short_circuit = short_circuit
        self.requests: list[ModelContextProjectionRequest] = []

    async def wrap_model_context(
        self,
        ctx: AgentContext,
        request: ModelContextProjectionRequest,
        handler: ModelContextNext,
    ) -> ModelContextProjection:
        del ctx
        self.requests.append(request)
        self._calls.append("host:before")
        if self._short_circuit:
            projection = ModelContextProjection(
                blocks=(
                    ModelContextBlock(
                        source_id="test.host-preamble",
                        placement=ModelContextPlacement.INPUT_PREAMBLE,
                        content="host preamble",
                    ),
                )
            )
        else:
            projection = await handler(request)
        self._calls.append("host:after")
        return ModelContextProjection(
            blocks=(
                *projection.blocks,
                ModelContextBlock(
                    source_id="test.host-epilogue",
                    placement=ModelContextPlacement.REQUEST_EPILOGUE,
                    content="host epilogue",
                ),
            )
        )


def test_definition_cannot_replace_mandatory_model_context_coordinator() -> None:
    with pytest.raises(DefinitionError) as exc_info:
        HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(lambda messages, info: "unused"),
            capabilities=(ModelContextCoordinatorCapability(),),
        )

    assert exc_info.value.code == "capability_scope_invalid"


async def test_host_wraps_plugin_capability_and_terminal_projection() -> None:
    calls: list[str] = []
    seen: list[list[ModelMessage]] = []
    host = _HostProjection(calls)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        seen.append(messages)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        plugins=(_ProjectionPlugin(calls),),
    )
    result = await executable.run("hello", bindings=RunBindings.embedded(model_context=host))

    assert result.output_or_raise() == "done"
    assert calls == ["host:before", "capability:before", "capability:after", "host:after"]
    assert host.requests == [
        ModelContextProjectionRequest(
            kind=ModelContextRequestKind.INPUT,
            input_origin=ModelContextInputOrigin.USER,
        )
    ]
    request = seen[0][-1]
    assert isinstance(request, ModelRequest)
    text = [
        (item.content if isinstance(item, TextContent) else item)
        for part in request.parts
        if isinstance(part, UserPromptPart)
        for item in user_prompt_content(part)
    ]
    assert text[0].startswith("Current Environment mounts")
    assert text[1] == "hello"
    assert text[-2:] == ["plugin context", "host epilogue"]
    assert any(isinstance(value, str) and value.startswith('<agent-context source="a13n-harness">') for value in text)
    assert result.state is not None
    persisted_request = result.state.message_history[0]
    assert isinstance(persisted_request, ModelRequest)
    persisted_text = [
        item.content if isinstance(item, TextContent) else item
        for part in persisted_request.parts
        if isinstance(part, UserPromptPart)
        for item in user_prompt_content(part)
    ]
    assert persisted_text == text
    assert persisted_request.metadata == request.metadata


async def test_capability_injection_preserves_the_active_prefix_across_model_requests() -> None:
    projection_requests: list[ModelContextProjectionRequest] = []
    seen: list[list[ModelMessage]] = []
    seen_instructions: list[str | None] = []

    def advance(step: int) -> str:
        return f"advanced {step}"

    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        assert "advance" in {tool.name for tool in info.function_tools}
        request_number = len(seen) + 1
        seen.append(deepcopy(messages))
        seen_instructions.append(info.instructions)
        if request_number < 3:
            yield {
                0: DeltaToolCall(
                    name="advance",
                    json_args=f'{{"step":{request_number}}}',
                    tool_call_id=f"call-{request_number}",
                )
            }
            return
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            _SequencedProjectionCapability(projection_requests),
            Capability(tools=[Tool(advance)]),
        ),
    )

    result = await executable.run("start", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    assert projection_requests == [
        ModelContextProjectionRequest(
            kind=ModelContextRequestKind.INPUT,
            input_origin=ModelContextInputOrigin.USER,
        ),
        ModelContextProjectionRequest(
            kind=ModelContextRequestKind.TOOL_RESULTS,
            tool_call_ids=("call-1",),
        ),
        ModelContextProjectionRequest(
            kind=ModelContextRequestKind.TOOL_RESULTS,
            tool_call_ids=("call-2",),
        ),
    ]
    assert len(seen) == 3
    assert seen_instructions == [seen_instructions[0]] * 3
    assert seen_instructions[0] is not None
    assert "Keep injected model context append-only." in seen_instructions[0]

    for previous, current in pairwise(seen):
        current_prefix = current[: len(previous)]
        assert ModelMessagesTypeAdapter.dump_json(current_prefix) == ModelMessagesTypeAdapter.dump_json(previous)

    canonical_messages = list(result.all_messages())
    canonical_request_prefix = canonical_messages[: len(seen[-1])]
    assert ModelMessagesTypeAdapter.dump_json(canonical_request_prefix) == ModelMessagesTypeAdapter.dump_json(seen[-1])

    injected_context = [
        [
            item.content if isinstance(item, TextContent) else item
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
            for item in user_prompt_content(part)
            if isinstance(item, TextContent) and item.content.startswith("capability context ")
        ]
        for messages in (*seen, canonical_messages)
    ]
    assert injected_context == [
        ["capability context 1"],
        ["capability context 1", "capability context 2"],
        ["capability context 1", "capability context 2", "capability context 3"],
        ["capability context 1", "capability context 2", "capability context 3"],
    ]
    for request_number, messages in enumerate(seen, start=1):
        current_request = messages[-1]
        assert isinstance(current_request, ModelRequest)
        assert user_prompt_content(current_request.parts[-1])[0].content == f"capability context {request_number}"


async def test_host_can_short_circuit_default_projection_without_bypassing_commit() -> None:
    calls: list[str] = []
    seen: list[list[ModelMessage]] = []
    host = _HostProjection(calls, short_circuit=True)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        seen.append(messages)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )
    await executable.run("hello", bindings=RunBindings.embedded(model_context=host))

    request = seen[0][-1]
    assert isinstance(request, ModelRequest)
    text = [
        (item.content if isinstance(item, TextContent) else item)
        for part in request.parts
        if isinstance(part, UserPromptPart)
        for item in user_prompt_content(part)
    ]
    assert text == ["host preamble", "hello", "host epilogue"]
    assert calls == ["host:before", "host:after"]


def test_tool_result_projection_preserves_complete_batch_before_epilogue() -> None:
    original = ModelRequest(
        parts=(
            ToolReturnPart(tool_name="first", content="one", tool_call_id="call-1"),
            ToolReturnPart(tool_name="second", content="two", tool_call_id="call-2"),
        )
    )
    request = ModelContextProjectionRequest(
        kind=ModelContextRequestKind.TOOL_RESULTS,
        tool_call_ids=("call-1", "call-2"),
    )
    projection = ModelContextProjection(
        blocks=(
            ModelContextBlock(
                source_id="test.tool-results",
                placement=ModelContextPlacement.REQUEST_EPILOGUE,
                content="after all results",
            ),
        )
    )

    committed = _commit_projection([original], request, projection)

    final = committed[-1]
    assert isinstance(final, ModelRequest)
    assert final.parts[:2] == original.parts
    assert isinstance(final.parts[2], UserPromptPart)
    assert user_prompt_content(final.parts[2])[0].content == "after all results"

    invalid = ModelContextProjection(
        blocks=(
            ModelContextBlock(
                source_id="test.invalid",
                placement=ModelContextPlacement.INPUT_PREAMBLE,
                content="invalid",
            ),
        )
    )
    with pytest.raises(DefinitionError, match="INPUT_PREAMBLE"):
        _validate_projection(invalid, request)


async def _assert_exact_coordinator_hooks(
    messages: list[ModelMessage],
    *,
    deferred_resume: Any = None,
) -> None:
    deps = _ProjectionDeps(deferred_resume)
    model = FunctionModel(lambda messages, info: "unused")
    ctx = RunContext(
        deps=cast(AgentContext, deps),
        model=model,
        usage=RunUsage(),
        run_id="run-1",
    )
    request_context = ModelRequestContext(
        model=model,
        messages=messages,
        model_settings=None,
        model_request_parameters=ModelRequestParameters(),
    )
    coordinator = ModelContextCoordinatorCapability()

    prepared = await coordinator.before_model_request(ctx, request_context)
    assert prepared is request_context
    assert prepared.messages == messages

    handled: list[ModelRequestContext] = []

    async def handler(value: ModelRequestContext) -> ModelResponse:
        handled.append(value)
        return ModelResponse(parts=(TextPart("done"),))

    response = await coordinator.wrap_model_request(
        ctx,
        request_context=prepared,
        handler=handler,
    )

    assert response.parts == (TextPart("done"),)
    assert handled == [request_context]
    assert deps.projection_calls == 0


async def test_exact_boundaries_skip_projection_without_history_mutation() -> None:
    original = ModelRequest(parts=(UserPromptPart("input"),))
    projection_request = ModelContextProjectionRequest(
        kind=ModelContextRequestKind.INPUT,
        input_origin=ModelContextInputOrigin.USER,
    )
    projection = ModelContextProjection(
        blocks=(
            ModelContextBlock(
                source_id="test.exact-overlay",
                placement=ModelContextPlacement.REQUEST_EPILOGUE,
                content="owned overlay",
            ),
        )
    )
    committed = _commit_projection([original], projection_request, projection)[0]
    assert isinstance(committed, ModelRequest)

    retry = ModelRequest(
        parts=(
            RetryPromptPart(
                content="try again",
                tool_name="lookup",
                tool_call_id="call-1",
            ),
        )
    )
    await _assert_exact_coordinator_hooks([committed, retry])

    suspended = ModelResponse(parts=(TextPart("partial"),), state="suspended")
    await _assert_exact_coordinator_hooks([committed, suspended])

    calls = (
        SimpleNamespace(tool_call_id="call-1"),
        SimpleNamespace(tool_call_id="call-2"),
    )
    deferred_resume = SimpleNamespace(
        requests=SimpleNamespace(calls=calls, approvals=()),
    )
    pending_calls = ModelResponse(
        parts=(
            ToolCallPart(tool_name="one", args={}, tool_call_id="call-1"),
            ToolCallPart(tool_name="two", args={}, tool_call_id="call-2"),
        )
    )
    complete_results = ModelRequest(
        parts=(
            ToolReturnPart(tool_name="one", content="one", tool_call_id="call-1"),
            ToolReturnPart(tool_name="two", content="two", tool_call_id="call-2"),
        )
    )
    await _assert_exact_coordinator_hooks(
        [committed, pending_calls, complete_results],
        deferred_resume=deferred_resume,
    )

    partial_results = ModelRequest(parts=(ToolReturnPart(tool_name="one", content="one", tool_call_id="call-1"),))
    await _assert_exact_coordinator_hooks(
        [committed, pending_calls, partial_results],
        deferred_resume=deferred_resume,
    )


def test_projection_validation_normalizes_invalid_source_type_to_definition_error() -> None:
    projection = ModelContextProjection(
        blocks=(
            ModelContextBlock(
                source_id=cast(str, 42),
                placement=ModelContextPlacement.REQUEST_EPILOGUE,
                content="invalid source",
            ),
        )
    )
    request = ModelContextProjectionRequest(
        kind=ModelContextRequestKind.INPUT,
        input_origin=ModelContextInputOrigin.USER,
    )

    with pytest.raises(DefinitionError) as exc_info:
        _validate_projection(projection, request)

    assert exc_info.value.code == "model_context_projection_invalid"


async def test_ordinary_preparation_preserves_prior_owned_overlays_for_prompt_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    monkeypatch.setattr(RunContext, "emit", AsyncMock())
    original = ModelRequest(parts=(UserPromptPart("input"),))
    request = ModelContextProjectionRequest(
        kind=ModelContextRequestKind.INPUT,
        input_origin=ModelContextInputOrigin.USER,
    )
    projection = ModelContextProjection(
        blocks=(
            ModelContextBlock(
                source_id="test.cache-stable",
                placement=ModelContextPlacement.REQUEST_EPILOGUE,
                content="historical overlay",
            ),
        )
    )
    committed = _commit_projection([original], request, projection)[0]
    assert isinstance(committed, ModelRequest)
    history = [
        committed,
        ModelResponse(parts=(TextPart("response"),)),
        ModelRequest(parts=(UserPromptPart("next input"),)),
    ]
    current_projection = ModelContextProjection(
        blocks=(
            ModelContextBlock(
                source_id="test.current",
                placement=ModelContextPlacement.REQUEST_EPILOGUE,
                content="current overlay",
            ),
        )
    )
    deps = _ProjectionDeps(projection=current_projection)
    model = FunctionModel(lambda messages, info: "unused")
    ctx = RunContext(
        deps=cast(AgentContext, deps),
        model=model,
        usage=RunUsage(),
        messages=history,
        run_id="run-1",
    )
    request_context = ModelRequestContext(
        model=model,
        messages=history,
        model_settings=None,
        model_request_parameters=ModelRequestParameters(),
    )

    coordinator = ModelContextCoordinatorCapability()
    prepared = await coordinator.before_model_request(ctx, request_context)
    handled: list[ModelRequestContext] = []

    async def handler(value: ModelRequestContext) -> ModelResponse:
        handled.append(value)
        return ModelResponse(parts=(TextPart("done"),))

    await coordinator.wrap_model_request(ctx, request_context=prepared, handler=handler)

    assert prepared is request_context
    assert prepared.messages[0] is committed
    assert ctx.messages[0] is committed
    assert deps.projection_calls == 1
    assert len(handled) == 1
    assert handled[0].messages[:-1] == history[:-1]
    assert handled[0].messages[-1] is ctx.messages[-1]
    assert isinstance(ctx.messages[-1], ModelRequest)
    assert user_prompt_content(ctx.messages[-1].parts[-1])[0].content == "current overlay"


def test_overlay_cleanup_uses_ownership_metadata_not_matching_text() -> None:
    original = ModelRequest(parts=(UserPromptPart("same text"),))
    request = ModelContextProjectionRequest(
        kind=ModelContextRequestKind.INPUT,
        input_origin=ModelContextInputOrigin.USER,
    )
    projection = ModelContextProjection(
        blocks=(
            ModelContextBlock(
                source_id="test.same-text",
                placement=ModelContextPlacement.REQUEST_EPILOGUE,
                content="same text",
            ),
        )
    )

    committed = _commit_projection([original], request, projection)
    cleaned = _remove_owned_overlays(committed)

    final = cleaned[-1]
    assert isinstance(final, ModelRequest)
    assert final.metadata is None
    assert final.parts == original.parts
    assert _remove_owned_overlays([original]) == [original]
    restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(committed))
    assert _remove_owned_overlays(restored) == cleaned
    injected = restored[-1].parts[-1].content[0]
    assert isinstance(injected, TextContent)
    assert injected.metadata == {"display": False, "source_id": "test.same-text"}
    assert restored[-1].parts[0].content == "same text"


async def test_input_events_do_not_replay_restored_history_or_change_provider_prefix() -> None:
    from a13n_harness import HarnessEvent, HarnessState
    from a13n_harness.model_context import ModelInputEvent
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    history = [
        ModelRequest(parts=[UserPromptPart("synthetic restored instructions")]),
        ModelResponse(parts=[TextPart("previous summary")]),
        ModelRequest(parts=[UserPromptPart("old user input")]),
        ModelResponse(parts=[TextPart("old answer")]),
    ]
    previous = HarnessState.new(message_history=tuple(history))
    before = previous.model_dump_json()
    seen = []

    async def respond(messages, info):
        seen.extend(deepcopy(messages))
        yield "done"

    executable = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond))
    observed = []
    prompt = [TextContent("current input", metadata={"source_id": "input-current"})]
    async with executable.stream(prompt, previous_state=previous, bindings=RunBindings.embedded()) as stream:
        async for event in stream:
            if isinstance(event, HarnessEvent) and isinstance(event.event, ModelInputEvent):
                observed.extend(
                    item for item in event.event.content if (item.metadata or {}).get("display") is not False
                )
    assert [(item.content, item.metadata) for item in observed] == [("current input", {"source_id": "input-current"})]
    assert previous.model_dump_json() == before
    assert ModelMessagesTypeAdapter.dump_json(seen[: len(history)]) == ModelMessagesTypeAdapter.dump_json(history)
    model = OpenAIResponsesModel("gpt-4o", provider=OpenAIProvider(api_key="fixture-only"))
    original_wire = await model._map_messages(history, {}, ModelRequestParameters())
    observed_prefix = await model._map_messages(seen[: len(history)], {}, ModelRequestParameters())
    assert observed_prefix == original_wire
