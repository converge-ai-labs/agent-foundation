from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_harness import (
    AbstractHarnessPlugin,
    AbstractModelContextCapability,
    AgentContext,
    HarnessBuilder,
    ModelContextBlock,
    ModelContextInputOrigin,
    ModelContextNext,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextProjectionRequest,
    ModelContextRequestKind,
    RunBindings,
)
from a13n_harness.errors import DefinitionError
from a13n_harness.model_context import (
    ModelContextCoordinatorCapability,
    _commit_projection,
    _is_retry_boundary,
    _remove_owned_overlays,
    _validate_projection,
)
from pydantic_ai import RunContext
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RunUsage

pytestmark = pytest.mark.anyio


class _ProjectionDeps:
    def __init__(self, deferred_resume: Any = None) -> None:
        self.deferred_resume = deferred_resume
        self.model_context = None
        self.projection_calls = 0

    async def project_model_context(
        self,
        request: ModelContextProjectionRequest,
    ) -> ModelContextProjection:
        del request
        self.projection_calls += 1
        return ModelContextProjection()


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
    result = await executable.run("hello", bindings=RunBindings.local(model_context=host))

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
    text = [part.content for part in request.parts if isinstance(part, UserPromptPart)]
    assert text[0].startswith("Current Environment topology")
    assert text[1] == "hello"
    assert text[-2:] == ["plugin context", "host epilogue"]
    assert any(isinstance(value, str) and value.startswith('<agent-context source="a13n-harness">') for value in text)


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
    await executable.run("hello", bindings=RunBindings.local(model_context=host))

    request = seen[0][-1]
    assert isinstance(request, ModelRequest)
    text = [part.content for part in request.parts if isinstance(part, UserPromptPart)]
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
    assert final.parts[2].content == "after all results"

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


async def test_exact_boundaries_skip_projection_and_owned_overlay_cleanup() -> None:
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
    assert _is_retry_boundary([committed, retry])
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
