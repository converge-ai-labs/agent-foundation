from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy

import httpx2
import pytest
from a13n_harness import (
    AgentContext,
    ContextRecoveryPolicy,
    HarnessBuilder,
    HarnessEvent,
    HarnessState,
    ModelRecoveryPolicy,
    RunBindings,
)
from a13n_harness.capabilities import CodeActCapability, HandoffCapability
from a13n_harness.capabilities.lifecycle import LifecycleEventCapability
from a13n_harness.events import HarnessExtensionEvent, ModelRetryScheduledPayload
from a13n_harness.recovery import DEFAULT_RECOVERY_PROMPT, InterruptedResponseTracker
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability, Capability
from pydantic_ai.exceptions import ModelContextWindowExceededError, OutputTokenLimitExceeded
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    PartEndEvent,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.function import AgentInfo, DeltaThinkingPart, DeltaToolCall, FunctionModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.output import ToolOutput
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import UsageLimits

pytestmark = pytest.mark.anyio


class FinishReasonModel(WrapperModel):
    """Use real native part events with scripted provider finish reasons."""

    def __init__(self, model, reasons, *, raw_reason: str | None = None):
        super().__init__(model)
        self.reasons = iter(reasons)
        self.raw_reason = raw_reason

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[object] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        reason = next(self.reasons)
        async with super().request_stream(messages, model_settings, model_request_parameters, run_context) as response:
            response.finish_reason = reason
            if self.raw_reason is not None:
                response.provider_details = {"finish_reason": self.raw_reason}
            yield response


def policy(**kwargs) -> ModelRecoveryPolicy:
    return ModelRecoveryPolicy(enabled=True, backoff_initial_seconds=0, backoff_max_seconds=0, **kwargs)


@pytest.mark.parametrize("streaming", [False, True])
async def test_length_continues_inside_one_run_without_concatenating_output(streaming: bool) -> None:
    histories = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        histories.append(deepcopy(messages))
        yield "partial" if len(histories) == 1 else "continuation"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FinishReasonModel(FunctionModel(stream_function=stream), ["length", "stop"]),
        model_recovery=policy(max_attempts=2),
    )
    events = []
    if streaming:
        async with executable.stream("start", bindings=RunBindings.embedded()) as run:
            async for event in run:
                events.append(event)
            result = run.result
        assert result is not None
    else:
        result = await executable.run("start", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "continuation"
    assert result.usage.requests == 2
    assert [
        p.content for m in histories[1] if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart)
    ][-1] == DEFAULT_RECOVERY_PROMPT
    partial = next(m for m in histories[1] if isinstance(m, ModelResponse))
    assert partial.state == "interrupted" and partial.parts == [TextPart("partial")]
    assert len({m.run_id for m in result.all_messages() if isinstance(m, ModelResponse)}) == 2
    assert result.state is not None
    restored = HarnessState.model_validate_json(result.state.model_dump_json())
    assert restored.message_history == result.all_messages()
    if streaming:
        notices = [
            e.event.payload
            for e in events
            if isinstance(e, HarnessEvent) and isinstance(e.event, HarnessExtensionEvent) and e.event.kind == "recovery"
        ]
        assert notices == [
            {
                "type": "model_retry_scheduled",
                "attempt": 2,
                "max_attempts": 2,
                "delay_seconds": 0.0,
                "reason": "output_token_limit",
            }
        ]


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("exhausted", [False, True])
@pytest.mark.parametrize("args", ["{}", '{"unfinished":'])
async def test_explicit_context_finish_reason_uses_context_recovery_without_dispatch(enabled, exhausted, args) -> None:
    from a13n_harness._context_recovery import CONTEXT_RECOVERY_STATE_ID
    from a13n_harness.capabilities.context import _COMPACTION_PROMPT

    histories = []
    invoked = []
    notices = []

    def action() -> str:
        invoked.append(True)
        return "executed"

    async def stream(messages, info):
        if any(isinstance(p, UserPromptPart) and p.content == _COMPACTION_PROMPT for m in messages for p in m.parts):
            raise ModelContextWindowExceededError("test", "summary overflow")
        histories.append(deepcopy(messages))
        if len(histories) == 1 or exhausted:
            yield "rejected partial"
            yield {0: DeltaToolCall(name="action", json_args=args, tool_call_id="context-cut")}
        else:
            yield "resumed"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        capabilities=(Capability(tools=[action]),),
        model=FinishReasonModel(
            FunctionModel(stream_function=stream),
            ["length", "stop", "length" if exhausted else "stop"],
            raw_reason="model_context_window_exceeded",
        ),
        model_recovery=policy(max_attempts=5),
        context_recovery=ContextRecoveryPolicy(enabled=enabled, preserve_history=False),
    )
    async with executable.stream("finish task", bindings=RunBindings.embedded()) as run:
        async for event in run:
            if isinstance(event, HarnessEvent) and isinstance(event.event, HarnessExtensionEvent):
                if event.event.kind == "recovery":
                    notices.append(event.event.payload)
        result = run.result
    assert result is not None and result.state is not None
    assert invoked == []
    assert not any(notice.get("type") == "model_retry_scheduled" for notice in notices)
    assert not any(isinstance(p, ToolCallPart | ToolReturnPart) for m in result.all_messages() for p in m.parts)
    if not enabled:
        assert result.status == "failed" and result.usage.requests == 1
        assert len(histories) == 1
    else:
        assert len(histories) == 2 and result.usage.requests == 3
        assert "Latest task-start input" in str(histories[-1])
        assert "rejected partial" not in str(histories[-1])
        marker = result.state.agent_context_state.get(CONTEXT_RECOVERY_STATE_ID)
        assert marker is not None
        assert marker.data["outcome"] == ("overflow" if exhausted else "resumed")
        if exhausted:
            assert result.failure is not None and result.failure.code == "context_recovery_exhausted"
        else:
            assert result.output_or_raise() == "resumed"


@pytest.mark.parametrize("successor", ["none", "text", "native", "unbalanced-native", "text-then-thinking"])
def test_output_limit_trims_only_orphan_trailing_thinking(successor: str) -> None:
    thinking = ThinkingPart("reasoning", id="rs_mock", provider_name="openai")
    parts = [thinking]
    if successor in ("text", "text-then-thinking"):
        parts.append(TextPart("safe partial"))
    if successor in ("native", "unbalanced-native"):
        parts.append(NativeToolCallPart("web_search", {}, tool_call_id="native"))
        if successor == "native":
            parts.append(NativeToolReturnPart("web_search", "result", tool_call_id="native"))
    if successor == "text-then-thinking":
        parts.append(ThinkingPart("orphan tail"))
    parts.append(ToolCallPart("action", "{}", tool_call_id="cut"))
    tracker = InterruptedResponseTracker()
    for index, part in enumerate(parts):
        tracker.observe(PartEndEvent(index=index, part=part), response_history_count=0)
    sanitized = tracker.sanitize_output_limit([ModelResponse(parts=parts, finish_reason="length")])
    if successor in ("none", "unbalanced-native"):
        assert sanitized == ()
    else:
        assert len(sanitized) == 1
        response = sanitized[0]
        assert isinstance(response, ModelResponse) and response.state == "interrupted"
        assert response.parts[0] == thinking
        assert not isinstance(response.parts[-1], ThinkingPart)
        assert not any(isinstance(part, ToolCallPart) for part in response.parts)
        assert any(isinstance(part, NativeToolReturnPart) for part in response.parts) is (successor == "native")


@pytest.mark.parametrize("mixed", [False, True])
async def test_length_and_transport_failures_share_a_bounded_streak(mixed: bool) -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages, info
        calls += 1
        yield "partial"
        if mixed and calls == 2:
            raise httpx2.ReadError("disconnected")

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FinishReasonModel(FunctionModel(stream_function=stream), ["length"] * 3),
        model_recovery=policy(max_attempts=3),
    )
    result = await executable.run("start", bindings=RunBindings.embedded())
    assert calls == 3 and result.usage.requests == 3
    assert result.status == "failed" and result.failure is not None
    assert result.failure.code == "model_recovery_exhausted" and result.failure.retry_hint == "new_run"
    assert result.state is not None
    assert all(m.state == "interrupted" for m in result.all_messages() if isinstance(m, ModelResponse))


@pytest.mark.parametrize("recovery", [ModelRecoveryPolicy(), policy(retry_output_limit=False), policy(max_attempts=1)])
@pytest.mark.parametrize("args", ["{}", '{"unfinished":'])
async def test_disabled_or_exhausted_length_drops_undispatched_calls(recovery, args: str) -> None:
    invoked = []

    def action() -> str:
        invoked.append(True)
        return "executed"

    async def stream(messages, info):
        yield "partial"
        yield {0: DeltaToolCall(name="action", json_args=args, tool_call_id="cut-call")}

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        capabilities=(Capability(tools=[action]),),
        model=FinishReasonModel(FunctionModel(stream_function=stream), ["length"]),
        model_recovery=recovery,
    )
    result = await executable.run("start", bindings=RunBindings.embedded())
    assert result.status == "failed" and invoked == []
    assert not any(isinstance(p, ToolCallPart | ToolReturnPart) for m in result.all_messages() for p in m.parts)
    assert result.state is not None
    assert result.state.message_history == result.all_messages()


async def test_previous_tool_results_survive_length_without_reexecution() -> None:
    invoked = []
    calls = 0
    histories = []

    def action() -> str:
        invoked.append(True)
        return "executed"

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        histories.append(deepcopy(messages))
        if calls <= 2:
            yield {0: DeltaToolCall(name="action", json_args="{}", tool_call_id=f"call-{calls}")}
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        capabilities=(Capability(tools=[action]),),
        model=FinishReasonModel(FunctionModel(stream_function=stream), ["tool_call", "length", "stop"]),
        model_recovery=policy(max_attempts=2),
    )
    result = await executable.run("start", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done" and invoked == [True]
    assert any(isinstance(p, ToolReturnPart) and p.tool_call_id == "call-1" for m in histories[2] for p in m.parts)
    assert not any(isinstance(p, ToolCallPart) and p.tool_call_id == "call-2" for m in histories[2] for p in m.parts)


@pytest.mark.parametrize("native_error", [False, True])
@pytest.mark.parametrize("complete_args", [False, True])
async def test_tool_output_limit_guides_smaller_calls_without_replaying_arguments(
    native_error: bool, complete_args: bool
) -> None:
    histories = []
    written = []
    large_args = '{"content":"discarded-large-argument' + ('"}' if complete_args else "")

    def write_chunk(content: str) -> str:
        written.append(content)
        return "written"

    async def stream(messages, info):
        histories.append(deepcopy(messages))
        if len(histories) == 1:
            yield {0: DeltaToolCall(name="write_chunk", json_args=large_args, tool_call_id="large")}
            if native_error:
                raise OutputTokenLimitExceeded("native truncation")
        elif len(histories) == 2:
            yield {0: DeltaToolCall(name="write_chunk", json_args='{"content":"small"}', tool_call_id="small")}
        else:
            yield "done"

    model = FunctionModel(stream_function=stream)
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        capabilities=(Capability(tools=[write_chunk]),),
        model=model if native_error else FinishReasonModel(model, ["length", "tool_call", "stop"]),
        model_recovery=policy(max_attempts=2),
    )
    result = await executable.run("start", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done" and written == ["small"]
    prompt = [
        p.content for m in histories[1] if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart)
    ][-1]
    assert isinstance(prompt, str)
    assert prompt.startswith(DEFAULT_RECOVERY_PROMPT)
    assert "tool calls were not executed (tools: write_chunk)" in prompt
    assert "Use smaller arguments per call" in prompt
    assert "where the available tools support them" in prompt
    assert "discarded-large-argument" not in repr(histories[1])
    assert not any(isinstance(p, ToolCallPart | ToolReturnPart) for m in histories[1] for p in m.parts)
    assert result.state is not None
    assert "discarded-large-argument" not in result.state.model_dump_json()


@pytest.mark.parametrize("custom", ["fixed", "factory"])
async def test_tool_output_limit_preserves_custom_continuation_input(custom: str) -> None:
    histories = []
    factory_calls = []

    def prompt_factory(error, attempt_index, messages):
        factory_calls.append((error, attempt_index, messages))
        return "custom continuation"

    def action() -> str:
        pytest.fail("the truncated call must not execute")

    async def stream(messages, info):
        histories.append(deepcopy(messages))
        if len(histories) == 1:
            yield {0: DeltaToolCall(name="action", json_args="{}", tool_call_id="cut")}
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        capabilities=(Capability(tools=[action]),),
        model=FinishReasonModel(FunctionModel(stream_function=stream), ["length", "stop"]),
        model_recovery=policy(
            max_attempts=2,
            **(
                {"continuation_prompt": "custom continuation"}
                if custom == "fixed"
                else {"prompt_factory": prompt_factory}
            ),
        ),
    )
    result = await executable.run("start", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    assert [
        p.content for m in histories[1] if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart)
    ][-1] == "custom continuation"
    if custom == "factory":
        assert len(factory_calls) == 1
        error, attempt_index, messages = factory_calls[0]
        assert isinstance(error, OutputTokenLimitExceeded) and attempt_index == 1
        assert not any(isinstance(p, ToolCallPart | ToolReturnPart) for m in messages for p in m.parts)
    else:
        assert factory_calls == []


@pytest.mark.parametrize("structural", ["codeact", "handoff"])
async def test_length_precedes_builtin_structural_model_retries(structural: str) -> None:
    calls = 0
    invoked = []

    def action() -> str:
        invoked.append(True)
        return "executed"

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            special = "run_code" if structural == "codeact" else "summarize"
            assert special in {tool.name for tool in info.function_tools}
            yield {0: DeltaToolCall(name=special, json_args="{}", tool_call_id="special")}
            yield {1: DeltaToolCall(name="action", json_args="{}", tool_call_id="ordinary")}
        else:
            yield "done"

    capability = CodeActCapability() if structural == "codeact" else HandoffCapability()
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        capabilities=(capability, Capability(tools=[action])),
        model=FinishReasonModel(FunctionModel(stream_function=stream), ["length", "stop"]),
        model_recovery=policy(max_attempts=2),
    )
    result = await executable.run("start", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done" and calls == 2 and invoked == []
    assert not any(isinstance(p, ToolCallPart | ToolReturnPart) for m in result.all_messages() for p in m.parts)
    assert len({m.run_id for m in result.all_messages() if isinstance(m, ModelResponse)}) <= 2


async def test_structured_output_regenerates_a_complete_value() -> None:
    class Answer(BaseModel):
        value: str

    calls = 0

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        yield {
            0: DeltaToolCall(
                name=info.output_tools[0].name,
                json_args='{"value":' if calls == 1 else '{"value":"whole value"}',
                tool_call_id=f"output-{calls}",
            )
        }

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=ToolOutput(Answer),
        model=FinishReasonModel(FunctionModel(stream_function=stream), ["length", "stop"]),
        model_recovery=policy(max_attempts=2),
    )
    result = await executable.run("start", bindings=RunBindings.embedded())
    assert result.output_or_raise() == Answer(value="whole value") and calls == 2


@pytest.mark.parametrize("thinking", [False, True])
async def test_empty_or_thinking_only_length_is_not_success(thinking: bool) -> None:
    calls = 0

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {0: DeltaThinkingPart(content="reasoning")} if thinking else ""
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FinishReasonModel(FunctionModel(stream_function=stream), ["length", "stop"]),
        model_recovery=policy(max_attempts=2),
    )
    result = await executable.run("start", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done" and calls == 2


@pytest.mark.parametrize("during_stream", [False, True])
async def test_native_output_limit_exception_uses_the_same_primary_budget(during_stream: bool) -> None:
    calls = 0

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        if during_stream:
            yield "partial"
        if calls == 1:
            raise OutputTokenLimitExceeded("native truncation")
        if not during_stream:
            yield "partial"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=policy(max_attempts=2),
    )
    result = await executable.run("start", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "partial" and calls == 2


@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.parametrize("observe_auxiliary", [False, True])
async def test_auxiliary_output_limit_does_not_start_primary_continuation(phase: str, observe_auxiliary: bool) -> None:
    auxiliary_calls = 0
    primary_calls = 0

    def auxiliary_response(messages, info):
        nonlocal auxiliary_calls
        auxiliary_calls += 1
        raise OutputTokenLimitExceeded("auxiliary truncation")

    auxiliary = Agent(
        model=FunctionModel(auxiliary_response),
        deps_type=AgentContext,
        capabilities=[LifecycleEventCapability()] if observe_auxiliary else [],
    )

    class AuxiliaryRequest(AbstractCapability[AgentContext]):
        async def before_model_request(
            self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
        ) -> ModelRequestContext:
            if phase == "before":
                await auxiliary.run("summarize", deps=ctx.deps)
            return request_context

        async def after_model_request(
            self, ctx: RunContext[AgentContext], *, request_context: ModelRequestContext, response: ModelResponse
        ) -> ModelResponse:
            if phase == "after":
                await auxiliary.run("summarize", deps=ctx.deps)
            return response

    async def stream(messages, info):
        nonlocal primary_calls
        primary_calls += 1
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(AuxiliaryRequest(),),
        model_recovery=policy(max_attempts=3),
    )
    result = await executable.run("start", bindings=RunBindings.embedded())
    assert auxiliary_calls == 1 and primary_calls == (0 if phase == "before" else 1)
    assert result.status == "failed" and result.failure is not None
    assert result.failure.code == "agent_run_failed"


async def test_length_notice_can_cancel_before_immediate_continuation() -> None:
    async def stream(messages, info):
        yield "partial"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FinishReasonModel(FunctionModel(stream_function=stream), ["length"]),
        model_recovery=ModelRecoveryPolicy(enabled=True, backoff_initial_seconds=60, backoff_max_seconds=60),
    )
    async with executable.stream("start", bindings=RunBindings.embedded()) as run:
        async for event in run:
            if isinstance(event, HarnessEvent) and isinstance(event.event, HarnessExtensionEvent):
                if event.event.kind == "recovery":
                    assert event.event.payload["delay_seconds"] == 0
                    run.cancel()
        result = run.result
    assert result is not None and result.status == "cancelled" and result.usage.requests == 1


async def test_usage_limit_wins_over_length_continuation() -> None:
    async def stream(messages, info):
        yield "partial"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FinishReasonModel(FunctionModel(stream_function=stream), ["length"]),
        model_recovery=policy(),
    )
    result = await executable.run("start", bindings=RunBindings.embedded(), usage_limits=UsageLimits(request_limit=1))
    assert result.status == "failed" and result.failure is not None
    assert result.failure.code == "usage_limit_exceeded" and result.usage.requests == 1


async def test_missing_finish_reason_remains_accepted() -> None:
    async def stream(messages, info):
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FinishReasonModel(FunctionModel(stream_function=stream), [None]),
        model_recovery=policy(),
    )
    assert (await executable.run("start", bindings=RunBindings.embedded())).output_or_raise() == "done"


def test_existing_positional_policy_arguments_keep_their_meaning() -> None:
    recovery = ModelRecoveryPolicy(True, 2, "continue", None, 0, 1)
    assert recovery.continuation_prompt == "continue" and recovery.retry_output_limit is True
    assert recovery.backoff_initial_seconds == 0 and recovery.backoff_max_seconds == 1


def test_old_retry_payload_still_parses() -> None:
    old = {"type": "model_retry_scheduled", "attempt": 2, "max_attempts": 5, "delay_seconds": 1}
    assert ModelRetryScheduledPayload.model_validate(old).reason is None


async def test_accepted_tool_response_resets_the_shared_failure_streak() -> None:
    calls = 0
    invoked = []

    def action() -> str:
        invoked.append(True)
        return "executed"

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        if calls == 2:
            yield {0: DeltaToolCall(name="action", json_args="{}", tool_call_id="progress")}
        else:
            yield "done" if calls == 4 else "partial"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        capabilities=(Capability(tools=[action]),),
        model=FinishReasonModel(FunctionModel(stream_function=stream), ["length", "tool_call", "length", "stop"]),
        model_recovery=policy(max_attempts=2),
    )
    result = await executable.run("start", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done" and calls == 4 and invoked == [True]


async def test_string_output_function_receives_only_the_accepted_complete_response() -> None:
    from pydantic_ai.output import TextOutput

    processed = []
    calls = 0

    def finalize(text: str) -> str:
        processed.append(text)
        return f"processed:{text}"

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        yield "partial" if calls == 1 else "final"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=TextOutput(finalize),
        model=FinishReasonModel(FunctionModel(stream_function=stream), ["length", "stop"]),
        model_recovery=policy(max_attempts=2),
    )
    result = await executable.run("start", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "processed:final" and processed == ["final"]
