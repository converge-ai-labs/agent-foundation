from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from converge_agent_harness import (
    HarnessBuilder,
    HarnessState,
    ModelResolutionError,
    ModelRunBinding,
    RunBindings,
    SelfHealingModel,
)
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters, ModelResolutionContext, StreamedResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings

pytestmark = pytest.mark.anyio


class RecordingModelBinding(ModelRunBinding):
    def __init__(self, model: Model) -> None:
        self.model = model
        self.calls: list[tuple[str, str, str]] = []

    async def resolve_model(
        self,
        context: ModelResolutionContext,
        model_id: str,
    ) -> Model:
        self.calls.append((context.deps.run_id, context.deps.thread_id, model_id))
        return self.model


async def test_logical_model_is_resolved_from_the_fresh_run_binding() -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "resolved"

    binding = RecordingModelBinding(FunctionModel(stream_function=stream))
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:primary"),
        output_type=str,
    )

    result = await executable.run(
        "hello",
        bindings=RunBindings.local(model_binding=binding),
    )

    assert result.output_or_raise() == "resolved"
    assert result.state is not None
    assert binding.calls == [
        (result.run_id, result.state.thread_id, "logical:primary"),
    ]


async def test_model_binding_observes_state_owned_identity_across_continuation_and_fork() -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "resolved"

    binding = RecordingModelBinding(FunctionModel(stream_function=stream))
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:primary"),
        output_type=str,
    )
    previous = HarnessState()

    first = await executable.run(
        "first",
        bindings=RunBindings.local(model_binding=binding),
        previous_state=previous,
    )
    second = await executable.run(
        "second",
        bindings=RunBindings.local(model_binding=binding),
        previous_state=previous,
    )
    forked = await executable.run(
        "forked",
        bindings=RunBindings.local(model_binding=binding),
        previous_state=previous.fork(),
    )

    assert first.state is not None
    assert second.state is not None
    assert forked.state is not None
    assert first.state.thread_id == previous.thread_id
    assert second.state.thread_id == previous.thread_id
    assert forked.state.thread_id != previous.thread_id
    assert [call[1] for call in binding.calls] == [
        previous.thread_id,
        previous.thread_id,
        forked.state.thread_id,
    ]


async def test_agent_model_settings_reach_the_resolved_model_unchanged() -> None:
    seen: list[ModelSettings | None] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        seen.append(info.model_settings)
        yield "configured"

    binding = RecordingModelBinding(FunctionModel(stream_function=stream))
    settings = ModelSettings(temperature=0.25)
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:primary", model_settings=settings),
        output_type=str,
    )

    result = await executable.run(
        "hello",
        bindings=RunBindings.local(model_binding=binding),
    )

    assert result.output_or_raise() == "configured"
    assert seen == [settings]


async def test_missing_run_binding_delegates_to_native_model_inference() -> None:
    executable = HarnessBuilder().build_code(
        AgentSpec(model="test"),
        output_type=str,
    )

    result = await executable.run("hello", bindings=RunBindings.local())

    assert result.status == "completed"
    assert isinstance(result.output_or_raise(), str)


async def test_concrete_model_bypasses_the_run_binding() -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "concrete"

    concrete = FunctionModel(stream_function=stream)
    binding = RecordingModelBinding(concrete)
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:ignored"),
        output_type=str,
        model=concrete,
    )

    result = await executable.run(
        "hello",
        bindings=RunBindings.local(model_binding=binding),
    )

    assert result.output_or_raise() == "concrete"
    assert binding.calls == []


class InvalidModelBinding(ModelRunBinding):
    async def resolve_model(
        self,
        context: ModelResolutionContext,
        model_id: str,
    ) -> Model:
        del context, model_id
        return Any  # type: ignore[return-value]


async def test_invalid_model_binding_result_fails_with_a_typed_error() -> None:
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:primary"),
        output_type=str,
    )

    with pytest.raises(ModelResolutionError) as exc_info:
        await executable.run(
            "hello",
            bindings=RunBindings.local(model_binding=InvalidModelBinding()),
        )

    assert exc_info.value.code == "model_resolution_invalid"


class FailingModel(Model):
    def __init__(self, error: Exception | None) -> None:
        super().__init__()
        self.error = error
        self.calls = 0

    @property
    def model_name(self) -> str:
        return "failing"

    @property
    def system(self) -> str:
        return "test"

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        del model_settings, model_request_parameters
        self.calls += 1
        if self.calls == 1 and self.error is not None:
            raise self.error
        return ModelResponse(parts=[TextPart(content="ok")])

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context=None,
    ) -> AsyncGenerator[StreamedResponse]:
        del messages, model_settings, model_request_parameters, run_context
        raise NotImplementedError
        yield  # pragma: no cover


def _stale_reasoning_history() -> list[ModelMessage]:
    return [
        ModelRequest(parts=[UserPromptPart(content="hello")]),
        ModelResponse(parts=[ThinkingPart(content="reasoning", id="rs_old"), TextPart(content="answer")]),
    ]


async def test_self_healing_retries_once_after_an_exact_history_repair() -> None:
    wrapped = FailingModel(
        ModelHTTPError(
            status_code=404,
            model_name="failing",
            body={"code": 5008, "message": "Item with id 'rs_old' not found."},
        )
    )
    model = SelfHealingModel(wrapped)
    history = _stale_reasoning_history()

    response = await model.request(history, None, ModelRequestParameters())

    assert response.parts == [TextPart(content="ok")]
    assert wrapped.calls == 2
    assert all(not isinstance(part, ThinkingPart) for message in history for part in message.parts)


async def test_self_healing_clears_provider_native_ids_without_breaking_tool_pairing() -> None:
    error = ModelHTTPError(
        status_code=400,
        model_name="failing",
        body={
            "message": "Invalid 'input[12].id': 'msg_old'. Expected an ID that begins with 'fc'.",
        },
    )
    wrapped = FailingModel(error)
    model = SelfHealingModel(wrapped)
    call = ToolCallPart(
        tool_name="search",
        args={"query": "x"},
        tool_call_id="call-1",
        id="msg_old",
        provider_name="old-provider",
    )
    result = ToolReturnPart(tool_name="search", tool_call_id="call-1", content="found")
    history: list[ModelMessage] = [
        ModelResponse(
            parts=[ThinkingPart(content="reasoning", id="rs_old"), TextPart(content="answer"), call],
            provider_name="old-provider",
            provider_response_id="response-1",
        ),
        ModelRequest(parts=[result]),
    ]

    await model.request(history, None, ModelRequestParameters())

    assert wrapped.calls == 2
    assert call.id is None
    assert call.provider_name is None
    assert call.tool_call_id == result.tool_call_id == "call-1"
    assert isinstance(history[0], ModelResponse)
    assert history[0].provider_response_id is None
    assert all(not isinstance(part, ThinkingPart) for message in history for part in message.parts)


async def test_self_healing_replaces_inline_images_after_oversized_payload_rejection() -> None:
    error = ModelHTTPError(
        status_code=413,
        model_name="failing",
        body={"message": "payload too large"},
    )
    wrapped = FailingModel(error)
    model = SelfHealingModel(wrapped)
    history: list[ModelMessage] = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="view",
                    tool_call_id="view-1",
                    content=[BinaryContent(data=b"image", media_type="image/png")],
                )
            ]
        )
    ]

    await model.request(history, None, ModelRequestParameters())

    assert wrapped.calls == 2
    tool_result = history[0].parts[0]
    assert isinstance(tool_result, ToolReturnPart)
    assert isinstance(tool_result.content[0], str)
    assert "image was removed" in tool_result.content[0]


async def test_self_healing_propagates_unmatched_errors_without_retrying() -> None:
    error = ModelHTTPError(
        status_code=429,
        model_name="failing",
        body={"message": "rate limited"},
    )
    wrapped = FailingModel(error)
    model = SelfHealingModel(wrapped)

    with pytest.raises(ModelHTTPError) as exc_info:
        await model.request(_stale_reasoning_history(), None, ModelRequestParameters())

    assert exc_info.value is error
    assert wrapped.calls == 1


async def test_self_healing_does_not_retry_when_the_matching_repair_is_a_noop() -> None:
    error = ModelHTTPError(
        status_code=404,
        model_name="failing",
        body={"code": 5008, "message": "Item with id 'rs_old' not found."},
    )
    wrapped = FailingModel(error)
    model = SelfHealingModel(wrapped)
    history = [ModelRequest(parts=[UserPromptPart(content="hello")])]

    with pytest.raises(ModelHTTPError) as exc_info:
        await model.request(history, None, ModelRequestParameters())

    assert exc_info.value is error
    assert wrapped.calls == 1


class StreamEstablishmentModel(FailingModel):
    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context=None,
    ) -> AsyncGenerator[StreamedResponse]:
        del messages, model_settings, model_request_parameters, run_context
        self.calls += 1
        if self.calls == 1 and self.error is not None:
            raise self.error
        yield None  # type: ignore[misc]


async def test_self_healing_retries_stream_establishment_once() -> None:
    wrapped = StreamEstablishmentModel(
        ModelHTTPError(
            status_code=404,
            model_name="failing",
            body={"code": 5008, "message": "Item with id 'rs_old' not found."},
        )
    )
    model = SelfHealingModel(wrapped)
    history = _stale_reasoning_history()

    async with model.request_stream(history, None, ModelRequestParameters()):
        pass

    assert wrapped.calls == 2
    assert all(not isinstance(part, ThinkingPart) for message in history for part in message.parts)
