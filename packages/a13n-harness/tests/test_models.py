from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from copy import copy
from typing import Any, cast

import a13n_harness.execution as execution_module
import pytest
from a13n_harness import (
    AgentContext,
    AgentDefinition,
    DefinitionError,
    HarnessBuilder,
    HarnessModelCharacteristics,
    HarnessState,
    ModelResolutionError,
    RunBindings,
    SubagentDefinition,
)
from a13n_harness import AgentSpec as HarnessAgentSpec
from a13n_harness.models import (
    MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED_ENV,
    MODEL_REQUEST_X_SESSION_ID_ENABLED_ENV,
    SelfHealingModel,
    SelfHealingModelCapability,
)
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, ResolveModelId, WrapModelRequestHandler
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
from pydantic_ai.models import (
    Model,
    ModelRequestContext,
    ModelRequestParameters,
    ModelResolutionContext,
    StreamedResponse,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.providers import Provider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RequestUsage

pytestmark = pytest.mark.anyio


class _ModelProfileObserver(AbstractCapability[AgentContext]):
    id = "test.model-profile-observer"

    def __init__(self, observed: list[tuple[int | None, float | None, bool]]) -> None:
        self._observed = observed

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        assert isinstance(ctx.model, Model)
        profile = ctx.model.profile
        self._observed.append(
            (
                profile.get("context_window"),
                ctx.context_window_used,
                profile.get("supports_json_object_output", False),
            )
        )
        return request_context


class RecordingModelResolver:
    def __init__(self, model: Model) -> None:
        self.model = model
        self.calls: list[tuple[str, str, str]] = []

    async def __call__(
        self,
        context: ModelResolutionContext,
        model_id: str,
    ) -> Model:
        self.calls.append((context.deps.run_id, context.deps.thread_id, model_id))
        return self.model


@pytest.mark.parametrize("model_source", ["definition", "resolver", "inference"])
async def test_harness_context_window_is_shared_through_native_model_profile(
    model_source: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[int | None, float | None, bool]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    model = FunctionModel(
        stream_function=stream,
        profile={"context_window": 1_000, "supports_json_object_output": True},
    )
    if model_source == "inference":
        monkeypatch.setattr(execution_module, "infer_model", lambda *args, **kwargs: model)
    spec = HarnessAgentSpec(
        model=None if model_source == "definition" else "logical:primary",
        model_characteristics=HarnessModelCharacteristics(context_window_tokens=2_000),
    )
    executable = HarnessBuilder().build(
        spec,
        output_type=str,
        model=model if model_source == "definition" else None,
        capabilities=(_ModelProfileObserver(observed),),
    )
    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart("previous")]),
            ModelResponse(
                parts=[TextPart("previous response")],
                usage=RequestUsage(input_tokens=1_200, output_tokens=100),
            ),
        )
    )
    bindings = RunBindings.embedded(
        model_resolver=RecordingModelResolver(model) if model_source == "resolver" else None,
    )

    result = await executable.run("continue", previous_state=previous, bindings=bindings)

    assert result.output_or_raise() == "done"
    assert observed == [(2_000, 0.65, True)]


async def test_logical_model_is_resolved_from_an_async_function() -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "resolved"

    model = FunctionModel(stream_function=stream)
    calls: list[tuple[str, str, str]] = []

    async def resolve_model(context: ModelResolutionContext, model_id: str) -> Model:
        calls.append((context.deps.run_id, context.deps.thread_id, model_id))
        return model

    executable = HarnessBuilder().build(
        AgentSpec(model="logical:primary"),
        output_type=str,
    )

    result = await executable.run(
        "hello",
        bindings=RunBindings.embedded(model_resolver=resolve_model),
    )

    assert result.output_or_raise() == "resolved"
    assert result.state is not None
    assert calls == [
        (result.run_id, result.state.thread_id, "logical:primary"),
    ]


async def test_model_resolver_observes_state_owned_identity_across_continuation_and_fork() -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "resolved"

    binding = RecordingModelResolver(FunctionModel(stream_function=stream))
    executable = HarnessBuilder().build(
        AgentSpec(model="logical:primary"),
        output_type=str,
    )
    previous = HarnessState.new(thread_id="thr_hostroot")

    first = await executable.run(
        "first",
        bindings=RunBindings.embedded(model_resolver=binding),
        previous_state=previous,
    )
    second = await executable.run(
        "second",
        bindings=RunBindings.embedded(model_resolver=binding),
        previous_state=previous,
    )
    forked_state = previous.fork(thread_id="thr_hostfork")
    forked = await executable.run(
        "forked",
        bindings=RunBindings.embedded(model_resolver=binding),
        previous_state=forked_state,
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

    binding = RecordingModelResolver(FunctionModel(stream_function=stream))
    settings = ModelSettings(temperature=0.25)
    executable = HarnessBuilder().build(
        AgentSpec(model="logical:primary", model_settings=settings),
        output_type=str,
    )

    previous = HarnessState.new(thread_id="thr_hostroot")
    result = await executable.run(
        "hello",
        bindings=RunBindings.embedded(model_resolver=binding),
        previous_state=previous,
    )

    assert result.output_or_raise() == "configured"
    assert result.state is not None
    assert result.state.thread_id == "thr_hostroot"
    assert seen == [
        ModelSettings(
            temperature=0.25,
            extra_headers={"x-session-id": result.state.thread_id},
        )
    ]
    assert settings == ModelSettings(temperature=0.25)


async def test_builder_uses_harness_model_inference_recursively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "inferred"

    model = FunctionModel(stream_function=stream)
    observed: list[tuple[str, object]] = []

    def gateway_provider_factory(gateway_name: str, provider_name: str) -> Provider[Any]:
        del gateway_name, provider_name
        return cast(Provider[Any], object())

    def harness_infer_model(
        model_id: Model | str,
        *,
        gateway_provider_factory: object = None,
    ) -> Model:
        assert isinstance(model_id, str)
        observed.append((model_id, gateway_provider_factory))
        return model

    monkeypatch.setattr(execution_module, "infer_model", harness_infer_model)
    child = AgentDefinition(
        agent=AgentSpec(model="company@openai:gpt-5"),
        output_type=str,
    )
    executable = HarnessBuilder(gateway_provider_factory=gateway_provider_factory).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(lambda messages, info: "parent"),
        subagents=(
            SubagentDefinition(
                name="child",
                description="Child",
                agent=child,
            ),
        ),
    )

    result = await executable.subagents["child"].executable.run(
        "hello",
        bindings=RunBindings.embedded(),
    )

    assert result.output_or_raise() == "inferred"
    assert observed == [("company@openai:gpt-5", gateway_provider_factory)]


async def test_explicit_request_affinity_overrides_automatic_values() -> None:
    seen: list[ModelSettings | None] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        seen.append(info.model_settings)
        yield "configured"

    settings = ModelSettings(
        temperature=0.25,
        openai_prompt_cache_key="explicit-cache",
        extra_headers={"X-Session-ID": "explicit", "X-Request": "request"},
    )
    executable = HarnessBuilder().build(
        AgentSpec(model_settings=settings),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    result = await executable.run("hello", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "configured"
    assert seen == [settings]
    assert settings["extra_headers"] == {
        "X-Session-ID": "explicit",
        "X-Request": "request",
    }


async def test_automatic_request_affinity_runs_inside_other_innermost_request_wrappers() -> None:
    seen: list[ModelSettings | None] = []

    class ReplacingSettingsCapability(AbstractCapability[AgentContext]):
        def get_ordering(self) -> CapabilityOrdering:
            return CapabilityOrdering(position="innermost")

        async def wrap_model_request(
            self,
            ctx: RunContext[AgentContext],
            *,
            request_context: ModelRequestContext,
            handler: WrapModelRequestHandler,
        ) -> ModelResponse:
            del ctx
            updated = copy(request_context)
            updated.model_settings = ModelSettings(extra_headers={"X-Other": "other"})
            return await handler(updated)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        seen.append(info.model_settings)
        yield "configured"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream, model_name="gpt-5"),
        capabilities=(ReplacingSettingsCapability(),),
    )

    result = await executable.run("hello", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "configured"
    assert result.state is not None
    assert seen == [
        ModelSettings(
            openai_prompt_cache_key=result.state.thread_id,
            extra_headers={
                "X-Other": "other",
                "x-session-id": result.state.thread_id,
            },
        )
    ]


@pytest.mark.parametrize(
    ("disabled_environment", "expect_session_header", "expect_prompt_cache_key"),
    [
        (MODEL_REQUEST_X_SESSION_ID_ENABLED_ENV, False, True),
        (MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED_ENV, True, False),
    ],
)
async def test_model_request_patches_can_be_disabled_independently_at_builder_creation(
    monkeypatch: pytest.MonkeyPatch,
    disabled_environment: str,
    expect_session_header: bool,
    expect_prompt_cache_key: bool,
) -> None:
    seen: list[ModelSettings | None] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        seen.append(info.model_settings)
        yield "configured"

    monkeypatch.setenv(disabled_environment, "false")
    builder = HarnessBuilder()
    monkeypatch.setenv(disabled_environment, "true")
    executable = builder.build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream, model_name="gpt-5"),
    )

    result = await executable.run("hello", bindings=RunBindings.embedded())

    assert result.state is not None
    expected = ModelSettings()
    if expect_session_header:
        expected["extra_headers"] = {"x-session-id": result.state.thread_id}
    if expect_prompt_cache_key:
        expected["openai_prompt_cache_key"] = result.state.thread_id
    assert seen == [expected]


@pytest.mark.parametrize(
    "environment_name",
    [
        MODEL_REQUEST_X_SESSION_ID_ENABLED_ENV,
        MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED_ENV,
    ],
)
@pytest.mark.parametrize("value", ["", " true", "true ", "enabled", "2"])
def test_model_request_patch_environment_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
    value: str,
) -> None:
    monkeypatch.setenv(environment_name, value)

    with pytest.raises(DefinitionError) as exc_info:
        HarnessBuilder()

    assert exc_info.value.code == "model_request_patch_environment_invalid"
    assert exc_info.value.details == {"name": environment_name}


async def test_concrete_model_bypasses_the_run_resolver() -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "concrete"

    concrete = FunctionModel(stream_function=stream)
    binding = RecordingModelResolver(concrete)
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=concrete,
    )

    result = await executable.run(
        "hello",
        bindings=RunBindings.embedded(model_resolver=binding),
    )

    assert result.output_or_raise() == "concrete"
    assert binding.calls == []


def test_string_and_concrete_model_sources_are_mutually_exclusive() -> None:
    concrete = FunctionModel(lambda messages, info: "unused")

    with pytest.raises(DefinitionError) as exc_info:
        AgentDefinition(
            agent=AgentSpec(model="logical:primary"),
            output_type=str,
            model=concrete,
        )

    assert exc_info.value.code == "model_selection_conflict"


class InvalidModelResolver:
    async def __call__(
        self,
        context: ModelResolutionContext,
        model_id: str,
    ) -> Model:
        del context, model_id
        return Any  # type: ignore[return-value]


async def test_invalid_model_resolver_result_fails_with_a_typed_error() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(model="logical:primary"),
        output_type=str,
    )

    with pytest.raises(ModelResolutionError) as exc_info:
        await executable.run(
            "hello",
            bindings=RunBindings.embedded(model_resolver=InvalidModelResolver()),
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


def _recovering_function_model() -> tuple[FunctionModel, list[int]]:
    calls: list[int] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise ModelHTTPError(
                status_code=404,
                model_name="failing",
                body={"code": 5008, "message": "Item with id 'rs_old' not found."},
            )
        yield "recovered"

    return FunctionModel(stream_function=stream), calls


async def test_self_healing_is_not_installed_implicitly() -> None:
    model, calls = _recovering_function_model()
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model,
    )

    result = await executable.run(
        "continue",
        bindings=RunBindings.embedded(),
        previous_state=HarnessState.new(message_history=_stale_reasoning_history()),
    )

    assert result.status == "failed"
    assert calls == [1]


async def test_self_healing_capability_wraps_a_concrete_model() -> None:
    model, calls = _recovering_function_model()
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model,
        capabilities=(SelfHealingModelCapability(),),
    )

    result = await executable.run(
        "continue",
        bindings=RunBindings.embedded(),
        previous_state=HarnessState.new(message_history=_stale_reasoning_history()),
    )

    assert result.output_or_raise() == "recovered"
    assert calls == [1, 2]


async def test_self_healing_capability_wraps_a_run_resolved_model() -> None:
    model, calls = _recovering_function_model()
    binding = RecordingModelResolver(model)
    executable = HarnessBuilder().build(
        AgentSpec(model="logical:primary"),
        output_type=str,
        capabilities=(SelfHealingModelCapability(),),
    )

    result = await executable.run(
        "continue",
        bindings=RunBindings.embedded(model_resolver=binding),
        previous_state=HarnessState.new(message_history=_stale_reasoning_history()),
    )

    assert result.output_or_raise() == "recovered"
    assert calls == [1, 2]
    assert len(binding.calls) == 1


async def test_self_healing_capability_wraps_a_natively_inferred_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, calls = _recovering_function_model()

    def infer_model(selection: object, provider_factory: object = None) -> Model:
        del provider_factory
        assert selection == "native:test"
        return model

    monkeypatch.setattr("a13n_harness.models.inference._pydantic_infer_model", infer_model)
    executable = HarnessBuilder().build(
        AgentSpec(model="native:test"),
        output_type=str,
        capabilities=(SelfHealingModelCapability(),),
    )

    result = await executable.run(
        "continue",
        bindings=RunBindings.embedded(),
        previous_state=HarnessState.new(message_history=_stale_reasoning_history()),
    )

    assert result.output_or_raise() == "recovered"
    assert calls == [1, 2]


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


@pytest.mark.parametrize(
    "message",
    (
        "messages.1.content.0: thinking block has an invalid signature",
        "messages.1.content.0: `thinking` block signature is invalid",
        "messages.1.content.0: redacted_thinking block has an invalid signature",
    ),
)
async def test_self_healing_recovers_anthropic_thinking_signature_failures(message: str) -> None:
    wrapped = FailingModel(
        ModelHTTPError(
            status_code=400,
            model_name="claude-fable-5-1",
            body={"error": {"message": message}},
        )
    )
    model = SelfHealingModel(wrapped)
    history = _stale_reasoning_history()

    response = await model.request(history, None, ModelRequestParameters())

    assert response.parts == [TextPart(content="ok")]
    assert wrapped.calls == 2
    assert all(not isinstance(part, ThinkingPart) for message in history for part in message.parts)


async def test_self_healing_does_not_match_unrelated_signature_failures() -> None:
    error = ModelHTTPError(
        status_code=400,
        model_name="failing",
        body={"message": "request signature is invalid"},
    )
    wrapped = FailingModel(error)
    model = SelfHealingModel(wrapped)

    with pytest.raises(ModelHTTPError) as exc_info:
        await model.request(_stale_reasoning_history(), None, ModelRequestParameters())

    assert exc_info.value is error
    assert wrapped.calls == 1


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


async def test_self_healing_preserves_an_explicit_empty_rule_set() -> None:
    error = ModelHTTPError(
        status_code=404,
        model_name="failing",
        body={"code": 5008, "message": "Item with id 'rs_old' not found."},
    )
    wrapped = FailingModel(error)
    model = SelfHealingModel(wrapped, rules=())

    with pytest.raises(ModelHTTPError) as exc_info:
        await model.request(_stale_reasoning_history(), None, ModelRequestParameters())

    assert exc_info.value is error
    assert wrapped.calls == 1


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


@pytest.mark.parametrize(
    "status_code,message",
    (
        (429, "quota exceeds account limit"),
        (400, "failed_precondition: billing is disabled"),
    ),
)
async def test_self_healing_does_not_treat_unrelated_provider_failures_as_oversized(
    status_code: int,
    message: str,
) -> None:
    error = ModelHTTPError(
        status_code=status_code,
        model_name="failing",
        body={"message": message},
    )
    wrapped = FailingModel(error)
    model = SelfHealingModel(wrapped)
    image = BinaryContent(data=b"image", media_type="image/png")
    history: list[ModelMessage] = [
        ModelRequest(parts=[ToolReturnPart(tool_name="view", tool_call_id="view-1", content=[image])])
    ]

    with pytest.raises(ModelHTTPError) as exc_info:
        await model.request(history, None, ModelRequestParameters())

    assert exc_info.value is error
    assert wrapped.calls == 1
    tool_result = history[0].parts[0]
    assert isinstance(tool_result, ToolReturnPart)
    assert tool_result.content == [image]


async def test_definition_cannot_add_a_second_model_resolver() -> None:
    async def resolver(context: ModelResolutionContext, model_id: str) -> None:
        del context, model_id
        return None

    with pytest.raises(DefinitionError) as exc_info:
        HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(lambda messages, info: "unused"),
            capabilities=(ResolveModelId(resolver),),
        )

    assert exc_info.value.code == "capability_scope_invalid"


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
