from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from copy import deepcopy

import pytest
from converge_agent_harness import HarnessBuilder, HarnessEvent, HarnessState, ModelRecoveryPolicy, RunBindings
from converge_agent_harness.recovery import INTERRUPTED_TOOL_RESULT, normalize_interrupted_history
from pydantic import BaseModel
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.exceptions import CallDeferred
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.function import AgentInfo, DeltaThinkingPart, DeltaToolCall, FunctionModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.output import ToolOutput
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import UsageLimits

pytestmark = pytest.mark.anyio


def _recovery_policy(*, max_attempts: int = 2) -> ModelRecoveryPolicy:
    return ModelRecoveryPolicy(
        enabled=True,
        max_attempts=max_attempts,
        backoff_initial_seconds=0,
        backoff_max_seconds=0,
    )


async def test_stream_failure_resumes_with_partial_history_and_shared_usage() -> None:
    calls: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        calls.append(deepcopy(messages))
        if len(calls) == 1:
            yield "partial answer"
            raise RuntimeError("stream disconnected")
        yield "resumed answer"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(),
    )

    result = await executable.run("start", bindings=RunBindings.local())

    assert result.output_or_raise() == "resumed answer"
    assert result.usage.requests == 2
    assert len(calls) == 2
    interrupted = next(message for message in calls[1] if isinstance(message, ModelResponse))
    assert interrupted.state == "interrupted"
    assert interrupted.parts == [TextPart(content="partial answer")]
    assert any(
        isinstance(message, ModelRequest)
        and any(getattr(part, "content", None) == _recovery_policy().continuation_prompt for part in message.parts)
        for message in calls[1]
    )
    response_run_ids = {
        message.run_id
        for message in result.all_messages()
        if isinstance(message, ModelResponse) and message.run_id is not None
    }
    assert len(response_run_ids) == 2


async def test_interrupted_partial_thinking_is_not_replayed() -> None:
    calls: list[list[ModelMessage]] = []

    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[dict[int, DeltaThinkingPart] | str]:
        del info
        calls.append(deepcopy(messages))
        if len(calls) == 1:
            yield {0: DeltaThinkingPart(content="unfinished private reasoning")}
            raise RuntimeError("stream disconnected")
        yield "resumed answer"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(),
    )

    result = await executable.run("start", bindings=RunBindings.local())

    assert result.output_or_raise() == "resumed answer"
    assert len(calls) == 2
    assert not any(
        isinstance(part, ThinkingPart)
        for message in calls[1]
        if isinstance(message, ModelResponse)
        for part in message.parts
    )
    assert not any(
        isinstance(part, ThinkingPart)
        for message in result.all_messages()
        if isinstance(message, ModelResponse)
        for part in message.parts
    )


async def test_disabled_recovery_exports_no_unfinished_thinking() -> None:
    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[dict[int, DeltaThinkingPart]]:
        del messages, info
        yield {0: DeltaThinkingPart(content="unfinished private reasoning")}
        raise RuntimeError("stream disconnected")

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    result = await executable.run("start", bindings=RunBindings.local())

    assert result.status == "failed"
    assert result.state is not None
    assert result.state.message_history == result.all_messages()
    assert not any(
        isinstance(part, ThinkingPart)
        for message in result.all_messages()
        if isinstance(message, ModelResponse)
        for part in message.parts
    )


async def test_finalized_thinking_and_partial_text_are_replayed_in_order() -> None:
    calls: list[list[ModelMessage]] = []

    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[dict[int, DeltaThinkingPart] | str]:
        del info
        calls.append(deepcopy(messages))
        if len(calls) == 1:
            yield {0: DeltaThinkingPart(content="finished reasoning")}
            yield {0: DeltaThinkingPart(signature="signature-1")}
            yield "visible partial answer"
            raise RuntimeError("stream disconnected")
        yield "resumed answer"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(),
    )

    result = await executable.run("start", bindings=RunBindings.local())

    assert result.output_or_raise() == "resumed answer"
    interrupted = next(
        message for message in calls[1] if isinstance(message, ModelResponse) and message.state == "interrupted"
    )
    assert interrupted.parts == [
        ThinkingPart(
            content="finished reasoning",
            signature="signature-1",
            provider_name="function",
        ),
        TextPart(content="visible partial answer"),
    ]


async def test_response_tracker_does_not_mix_multiple_model_requests() -> None:
    calls: list[list[ModelMessage]] = []

    def lookup() -> str:
        return "lookup complete"

    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[dict[int, DeltaThinkingPart | DeltaToolCall] | str]:
        del info
        calls.append(deepcopy(messages))
        if len(calls) == 1:
            yield {0: DeltaThinkingPart(content="first response reasoning")}
            yield {0: DeltaThinkingPart(signature="signature-1")}
            yield {
                1: DeltaToolCall(
                    name="lookup",
                    json_args="{}",
                    tool_call_id="tool-1",
                )
            }
            return
        if len(calls) == 2:
            # This incomplete index 0 call emits no public part event. The
            # first public event for this response is therefore text at index 1.
            yield {0: DeltaToolCall(json_args='{"value":')}
            yield "second response partial text"
            raise RuntimeError("second response disconnected")
        yield "recovered answer"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(tools=[lookup], id="test-tools"),),
        model_recovery=_recovery_policy(max_attempts=2),
    )

    result = await executable.run("start", bindings=RunBindings.local())

    assert result.output_or_raise() == "recovered answer"
    assert len(calls) == 3
    recovery_history = calls[2]
    thinking_parts = [
        part
        for message in recovery_history
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ThinkingPart)
    ]
    tool_calls = [
        part
        for message in recovery_history
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart) and part.tool_call_id == "tool-1"
    ]
    tool_returns = [
        part
        for message in recovery_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_call_id == "tool-1"
    ]
    interrupted = [
        message for message in recovery_history if isinstance(message, ModelResponse) and message.state == "interrupted"
    ]
    assert len(thinking_parts) == 1
    assert len(tool_calls) == 1
    assert len(tool_returns) == 1
    assert tool_returns[0].content == "lookup complete"
    assert len(interrupted) == 1
    assert interrupted[0].parts == [TextPart(content="second response partial text")]


async def test_unobserved_usage_limited_thinking_is_not_exported() -> None:
    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[dict[int, DeltaThinkingPart]]:
        del messages, info
        yield {0: DeltaThinkingPart(content="unobserved unfinished reasoning")}

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    result = await executable.run(
        "start",
        bindings=RunBindings.local(),
        usage_limits=UsageLimits(output_tokens_limit=0),
    )

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "usage_limit_exceeded"
    assert result.state is not None
    assert not any(
        isinstance(part, ThinkingPart)
        for message in result.state.message_history
        if isinstance(message, ModelResponse)
        for part in message.parts
    )


async def test_complete_native_tool_parts_survive_a_later_text_interruption() -> None:
    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[dict[int, NativeToolCallPart | NativeToolReturnPart] | str]:
        del messages, info
        yield {
            0: NativeToolCallPart(
                tool_name="web_search",
                args={"query": "safe recovery"},
                tool_call_id="native-1",
            )
        }
        yield {
            1: NativeToolReturnPart(
                tool_name="web_search",
                content="native result",
                tool_call_id="native-1",
            )
        }
        yield "partial answer"
        raise RuntimeError("stream disconnected")

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    result = await executable.run("start", bindings=RunBindings.local())

    assert result.status == "failed"
    interrupted = next(
        message
        for message in result.all_messages()
        if isinstance(message, ModelResponse) and message.state == "interrupted"
    )
    assert [type(part) for part in interrupted.parts] == [
        NativeToolCallPart,
        NativeToolReturnPart,
        TextPart,
    ]
    assert interrupted.parts[1].content == "native result"
    assert interrupted.parts[2].content == "partial answer"


@pytest.mark.parametrize(
    "parts",
    [
        (
            NativeToolCallPart(tool_name="web_search", args={}, tool_call_id="native-1"),
            NativeToolCallPart(tool_name="web_search", args={}, tool_call_id="native-1"),
            NativeToolReturnPart(
                tool_name="web_search",
                content="native result",
                tool_call_id="native-1",
            ),
        ),
        (
            NativeToolCallPart(tool_name="web_search", args={}, tool_call_id="native-1"),
            NativeToolReturnPart(
                tool_name="code_execution",
                content="native result",
                tool_call_id="native-1",
            ),
        ),
        (
            NativeToolReturnPart(
                tool_name="web_search",
                content="native result",
                tool_call_id="native-1",
            ),
            NativeToolCallPart(tool_name="web_search", args={}, tool_call_id="native-1"),
        ),
    ],
    ids=("duplicate-id", "tool-name-mismatch", "return-before-call"),
)
async def test_malformed_native_tool_pairs_discard_the_interrupted_response(
    parts: tuple[NativeToolCallPart | NativeToolReturnPart, ...],
) -> None:
    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[dict[int, NativeToolCallPart | NativeToolReturnPart] | str]:
        del messages, info
        for index, part in enumerate(parts):
            yield {index: part}
        yield "partial answer"
        raise RuntimeError("stream disconnected")

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    result = await executable.run("start", bindings=RunBindings.local())

    assert result.status == "failed"
    assert not any(
        isinstance(part, NativeToolCallPart | NativeToolReturnPart)
        for message in result.all_messages()
        if isinstance(message, ModelResponse)
        for part in message.parts
    )
    assert result.state is not None
    assert not any(
        isinstance(part, NativeToolCallPart | NativeToolReturnPart)
        for message in result.state.message_history
        if isinstance(message, ModelResponse)
        for part in message.parts
    )


async def test_unmatched_native_tool_call_discards_the_interrupted_response() -> None:
    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[dict[int, NativeToolCallPart] | str]:
        del messages, info
        yield {
            0: NativeToolCallPart(
                tool_name="web_search",
                args={"query": "unsafe continuation"},
                tool_call_id="native-unmatched",
            )
        }
        yield "partial answer after native call"
        raise RuntimeError("stream disconnected")

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    result = await executable.run("start", bindings=RunBindings.local())

    assert result.status == "failed"
    assert not any(
        isinstance(part, NativeToolCallPart)
        for message in result.all_messages()
        if isinstance(message, ModelResponse)
        for part in message.parts
    )
    assert result.state is not None
    assert not any(
        isinstance(part, NativeToolCallPart)
        for message in result.state.message_history
        if isinstance(message, ModelResponse)
        for part in message.parts
    )


async def test_recovery_does_not_replay_an_unmatched_native_tool_call() -> None:
    calls: list[list[ModelMessage]] = []

    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[dict[int, NativeToolCallPart] | str]:
        del info
        calls.append(deepcopy(messages))
        if len(calls) == 1:
            yield {
                0: NativeToolCallPart(
                    tool_name="web_search",
                    args={"query": "unsafe continuation"},
                    tool_call_id="native-unmatched",
                )
            }
            raise RuntimeError("stream disconnected")
        yield "resumed answer"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(),
    )

    result = await executable.run("start", bindings=RunBindings.local())

    assert result.output_or_raise() == "resumed answer"
    assert len(calls) == 2
    assert not any(
        isinstance(part, NativeToolCallPart)
        for message in calls[1]
        if isinstance(message, ModelResponse)
        for part in message.parts
    )


async def test_unfinalized_tool_call_invalidates_the_partial_response() -> None:
    calls: list[list[ModelMessage]] = []

    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[dict[int, DeltaToolCall] | str]:
        del info
        calls.append(deepcopy(messages))
        if len(calls) == 1:
            yield {
                0: DeltaToolCall(
                    name="side_effect",
                    json_args='{"value":"started"}',
                    tool_call_id="tool-1",
                )
            }
            raise RuntimeError("stream disconnected")
        yield "resumed answer"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(),
    )

    result = await executable.run("start", bindings=RunBindings.local())

    assert result.output_or_raise() == "resumed answer"
    assert len(calls) == 2
    assert not any(
        isinstance(part, ToolCallPart) and part.tool_call_id == "tool-1"
        for message in calls[1]
        if isinstance(message, ModelResponse)
        for part in message.parts
    )


async def test_disabled_recovery_exports_interrupted_model_history_as_a_failed_result() -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "partial"
        raise RuntimeError("stream disconnected")

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    result = await executable.run("start", bindings=RunBindings.local())

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "agent_run_failed"
    assert result.state is not None
    assert result.state.message_history == result.all_messages()
    tail = result.all_messages()[-1]
    assert isinstance(tail, ModelResponse)
    assert tail.state == "interrupted"
    assert tail.parts == [TextPart(content="partial")]


async def test_stream_establishment_failure_can_resume_before_any_content() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages, info
        calls += 1
        if calls == 1:
            raise RuntimeError("stream establishment failed")
        yield "recovered"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(),
    )

    result = await executable.run("start", bindings=RunBindings.local())

    assert result.output_or_raise() == "recovered"
    assert calls == 2
    assert result.usage.requests == 2


async def test_recovery_prompt_factory_receives_the_failure_and_repaired_history() -> None:
    calls: list[list[ModelMessage]] = []
    factory_calls: list[tuple[str, int, tuple[ModelMessage, ...]]] = []

    async def prompt_factory(
        error: BaseException,
        attempt_index: int,
        messages: Sequence[ModelMessage],
    ) -> str:
        factory_calls.append((str(error), attempt_index, tuple(messages)))
        return "custom continuation"

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        calls.append(deepcopy(messages))
        if len(calls) == 1:
            yield "partial"
            raise RuntimeError("disconnected")
        yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=ModelRecoveryPolicy(
            enabled=True,
            max_attempts=2,
            prompt_factory=prompt_factory,
            backoff_initial_seconds=0,
            backoff_max_seconds=0,
        ),
    )

    result = await executable.run("start", bindings=RunBindings.local())

    assert result.output_or_raise() == "done"
    assert len(factory_calls) == 1
    assert factory_calls[0][0:2] == ("disconnected", 1)
    assert isinstance(factory_calls[0][2][-1], ModelResponse)
    assert factory_calls[0][2][-1].state == "interrupted"
    assert any(
        isinstance(message, ModelRequest)
        and any(getattr(part, "content", None) == "custom continuation" for part in message.parts)
        for message in calls[1]
    )


async def test_recovery_backoff_is_full_jitter_and_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    ceilings: list[float] = []

    def use_ceiling(minimum: float, maximum: float) -> float:
        assert minimum == 0
        ceilings.append(maximum)
        return maximum

    monkeypatch.setattr("converge_agent_harness.recovery.random.uniform", use_ceiling)
    policy = ModelRecoveryPolicy(
        enabled=True,
        backoff_initial_seconds=2,
        backoff_max_seconds=5,
    )

    assert [policy.delay(index) for index in (1, 2, 3)] == [2, 4, 5]
    assert ceilings == [2, 4, 5]


async def test_recovery_attempt_budget_is_total_and_monotonic() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages, info
        calls += 1
        yield "partial"
        raise RuntimeError("still disconnected")

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(max_attempts=3),
    )

    result = await executable.run("start", bindings=RunBindings.local())

    assert calls == 3
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "model_recovery_exhausted"
    assert result.usage.requests == 3


async def test_usage_limit_never_enters_model_recovery() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages, info
        calls += 1
        yield "unreachable"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(max_attempts=5),
    )

    result = await executable.run(
        "start",
        bindings=RunBindings.local(),
        usage_limits=UsageLimits(request_limit=0),
    )

    assert calls == 0
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "usage_limit_exceeded"


async def test_cancel_interrupts_recovery_backoff_without_starting_another_attempt() -> None:
    started = asyncio.Event()
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages, info
        calls += 1
        started.set()
        raise RuntimeError("connection failed")
        yield "unreachable"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=ModelRecoveryPolicy(
            enabled=True,
            max_attempts=5,
            backoff_initial_seconds=60,
            backoff_max_seconds=60,
        ),
    )

    async with executable.stream("start", bindings=RunBindings.local()) as run_stream:
        pending = asyncio.create_task(run_stream.__anext__())
        await started.wait()
        run_stream.cancel()
        terminal = await asyncio.wait_for(pending, timeout=2)
        while isinstance(terminal, HarnessEvent):
            terminal = await asyncio.wait_for(run_stream.__anext__(), timeout=2)

    assert terminal.result.status == "cancelled"
    assert calls == 1


async def test_cancelled_stream_does_not_export_unfinished_thinking() -> None:
    started = asyncio.Event()

    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[dict[int, DeltaThinkingPart]]:
        del messages, info
        yield {0: DeltaThinkingPart(content="unfinished private reasoning")}
        started.set()
        await asyncio.Event().wait()

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    async with executable.stream("start", bindings=RunBindings.local()) as run_stream:
        await run_stream.__anext__()
        pending = asyncio.create_task(run_stream.__anext__())
        await started.wait()
        run_stream.cancel()
        terminal = await asyncio.wait_for(pending, timeout=2)
        while isinstance(terminal, HarnessEvent):
            terminal = await asyncio.wait_for(run_stream.__anext__(), timeout=2)

    assert terminal.result.status == "cancelled"
    assert terminal.result.state is not None
    assert not any(
        isinstance(part, ThinkingPart)
        for message in terminal.result.all_messages()
        if isinstance(message, ModelResponse)
        for part in message.parts
    )


async def test_cancel_fence_wins_when_provider_translates_cancellation() -> None:
    started = asyncio.Event()
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages, info
        calls += 1
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise RuntimeError("provider translated cancellation") from None
        yield "unreachable"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(max_attempts=5),
    )

    async with executable.stream("start", bindings=RunBindings.local()) as run_stream:
        pending = asyncio.create_task(run_stream.__anext__())
        await started.wait()
        run_stream.cancel()
        terminal = await asyncio.wait_for(pending, timeout=2)
        while isinstance(terminal, HarnessEvent):
            terminal = await asyncio.wait_for(run_stream.__anext__(), timeout=2)

    assert terminal.result.status == "cancelled"
    assert calls == 1


async def test_provider_suspended_continuation_remains_inside_one_pydantic_attempt() -> None:
    calls = 0
    entries = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages, info
        calls += 1
        yield "first " if calls == 1 else "second"

    class SuspendingModel(WrapperModel):
        @asynccontextmanager
        async def request_stream(
            self,
            messages: list[ModelMessage],
            model_settings: ModelSettings | None,
            model_request_parameters: ModelRequestParameters,
            run_context: RunContext[object] | None = None,
        ) -> AsyncGenerator[StreamedResponse]:
            nonlocal entries
            entries += 1
            async with super().request_stream(
                messages,
                model_settings,
                model_request_parameters,
                run_context,
            ) as response:
                response.state = "suspended" if entries == 1 else "complete"
                yield response

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=SuspendingModel(FunctionModel(stream_function=stream)),
        model_recovery=_recovery_policy(max_attempts=5),
    )

    result = await executable.run("start", bindings=RunBindings.local())

    assert result.output_or_raise() == "first second"
    assert calls == 2
    responses = [message for message in result.all_messages() if isinstance(message, ModelResponse)]
    assert len(responses) == 1
    assert responses[0].state == "complete"


async def test_output_retry_exhaustion_does_not_start_a_new_attempt() -> None:
    class RequiredOutput(BaseModel):
        value: str

    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[dict[int, DeltaToolCall]]:
        nonlocal calls
        del messages
        calls += 1
        output_tool = info.output_tools[0]
        yield {
            0: DeltaToolCall(
                name=output_tool.name,
                json_args="{}",
                tool_call_id=f"invalid-{calls}",
            )
        }

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test", retries={"output": 1}),
        output_type=ToolOutput(RequiredOutput, name="finish"),
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(max_attempts=5),
    )

    result = await executable.run("start", bindings=RunBindings.local())

    assert calls == 2
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "agent_run_failed"


async def test_tool_execution_failure_does_not_start_model_recovery() -> None:
    model_calls = 0
    tool_calls = 0

    def failing_tool() -> str:
        nonlocal tool_calls
        tool_calls += 1
        raise RuntimeError("tool failed")

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[dict[int, DeltaToolCall]]:
        nonlocal model_calls
        del messages, info
        model_calls += 1
        yield {
            0: DeltaToolCall(
                name="failing_tool",
                json_args="{}",
                tool_call_id="tool-1",
            )
        }

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(tools=[failing_tool], id="test-tools"),),
        model_recovery=_recovery_policy(max_attempts=5),
    )

    with pytest.raises(RuntimeError, match="tool failed"):
        await executable.run("start", bindings=RunBindings.local())

    assert model_calls == 1
    assert tool_calls == 1


async def test_deferred_tool_request_stays_suspended_and_is_not_closed_or_retried() -> None:
    model_calls = 0

    def deferred_tool() -> str:
        raise CallDeferred()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[dict[int, DeltaToolCall]]:
        nonlocal model_calls
        del messages, info
        model_calls += 1
        yield {
            0: DeltaToolCall(
                name="deferred_tool",
                json_args="{}",
                tool_call_id="deferred-1",
            )
        }

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(tools=[deferred_tool], id="test-tools"),),
        model_recovery=_recovery_policy(max_attempts=5),
    )

    result = await executable.run("start", bindings=RunBindings.local())

    assert result.status == "suspended"
    assert result.suspend_reason == "deferred"
    assert result.deferred is not None
    assert model_calls == 1
    assert not any(
        isinstance(part, ToolReturnPart)
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


async def test_upstream_history_cleanup_removes_stale_tool_results_before_retry() -> None:
    calls: list[list[ModelMessage]] = []
    stale_id = "stale-1"
    history = (
        ModelResponse(parts=[ToolCallPart(tool_name="search", args={}, tool_call_id=stale_id)]),
        ModelRequest(parts=[RetryPromptPart(content="retry response")]),
        ModelResponse(parts=[TextPart(content="corrected")]),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="search",
                    tool_call_id=stale_id,
                    content="stale synthetic result",
                    outcome="failed",
                )
            ]
        ),
    )

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        calls.append(deepcopy(messages))
        yield "continued"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    result = await executable.run(
        "continue",
        bindings=RunBindings.local(),
        previous_state=HarnessState.new(message_history=history),
    )

    assert result.output_or_raise() == "continued"
    sent = calls[0]
    stale_results = [
        part
        for message in sent
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_call_id == stale_id
    ]
    assert len(stale_results) == 1
    result_index = next(
        index
        for index, message in enumerate(sent)
        if isinstance(message, ModelRequest) and stale_results[0] in message.parts
    )
    assert result_index > 0
    assert isinstance(sent[result_index - 1], ModelResponse)


async def test_saved_interrupted_tool_history_is_repaired_before_rerun() -> None:
    calls: list[list[ModelMessage]] = []
    history = (
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="shell_exec",
                    args={"command": "deploy"},
                    tool_call_id="call-1",
                )
            ],
            state="interrupted",
        ),
    )

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        calls.append(deepcopy(messages))
        yield "continued safely"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    result = await executable.run(
        "continue",
        bindings=RunBindings.local(),
        previous_state=HarnessState.new(message_history=history),
    )

    assert result.output_or_raise() == "continued safely"
    tool_result = next(
        part
        for message in calls[0]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_call_id == "call-1"
    )
    assert tool_result.outcome == "failed"
    assert tool_result.content == INTERRUPTED_TOOL_RESULT


def test_interrupted_tool_call_is_closed_with_an_honest_unknown_effect_result() -> None:
    history = (
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="shell_exec",
                    args={"command": "deploy"},
                    tool_call_id="call-1",
                )
            ],
            state="interrupted",
        ),
    )

    normalized, closed = normalize_interrupted_history(history)
    normalized_again, closed_again = normalize_interrupted_history(normalized)

    assert closed == 1
    assert closed_again == 0
    assert normalized_again == normalized
    result = normalized[-1]
    assert isinstance(result, ModelRequest)
    tool_result = result.parts[0]
    assert isinstance(tool_result, ToolReturnPart)
    assert tool_result.outcome == "failed"
    assert tool_result.content == INTERRUPTED_TOOL_RESULT
    assert "may have partially or fully completed" in str(tool_result.content)
    assert "Check the current state" in str(tool_result.content)


def test_retry_prompt_counts_as_a_real_tool_result_and_deferred_frontier_is_preserved() -> None:
    call = ToolCallPart(tool_name="shell_exec", args={}, tool_call_id="call-1")
    completed_history = (
        ModelResponse(parts=[call]),
        ModelRequest(
            parts=[
                RetryPromptPart(
                    content="use corrected arguments",
                    tool_name="shell_exec",
                    tool_call_id="call-1",
                )
            ],
            state="interrupted",
        ),
    )
    deferred_history = (ModelResponse(parts=[call]),)

    normalized, closed = normalize_interrupted_history(completed_history)
    preserved, deferred_closed = normalize_interrupted_history(deferred_history)

    assert closed == 0
    assert normalized == completed_history
    assert deferred_closed == 0
    assert preserved == deferred_history
