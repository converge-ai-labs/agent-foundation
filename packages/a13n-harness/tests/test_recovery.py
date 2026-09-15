from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from copy import deepcopy

import httpx2
import pytest
from a13n_harness import (
    HarnessBuilder,
    HarnessEvent,
    HarnessRunResultEvent,
    HarnessState,
    ModelRecoveryPolicy,
    RunBindings,
)
from a13n_harness.events import HarnessExtensionEvent
from a13n_harness.recovery import (
    INTERRUPTED_TOOL_RESULT,
    InterruptedResponseTracker,
    is_recoverable_model_failure,
    normalize_interrupted_history,
)
from a13n_harness.tools import RECOVERY_RETRY_SAFE_METADATA_KEY
from pydantic import BaseModel
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability, SetToolMetadata
from pydantic_ai.exceptions import CallDeferred, ModelRetry, UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.function import AgentInfo, DeltaThinkingPart, DeltaToolCall, FunctionModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.output import ToolOutput
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext, Tool
from pydantic_ai.usage import UsageLimits

pytestmark = pytest.mark.anyio


def _recovery_policy(*, max_attempts: int = 2) -> ModelRecoveryPolicy:
    return ModelRecoveryPolicy(
        enabled=True,
        max_attempts=max_attempts,
        backoff_initial_seconds=0,
        backoff_max_seconds=0,
    )


async def test_recovery_prompt_factory_receives_detached_messages() -> None:
    original = ModelRequest(parts=[UserPromptPart(content="original")])

    def prompt_factory(
        error: BaseException,
        attempt_index: int,
        messages: Sequence[ModelMessage],
    ) -> str:
        del error, attempt_index
        request = messages[0]
        assert isinstance(request, ModelRequest)
        prompt = request.parts[0]
        assert isinstance(prompt, UserPromptPart)
        prompt.content = "mutated"
        return "continue"

    policy = ModelRecoveryPolicy(enabled=True, prompt_factory=prompt_factory)

    value = await policy.build_prompt(RuntimeError("failed"), 1, (original,))

    assert value == "continue"
    prompt = original.parts[0]
    assert isinstance(prompt, UserPromptPart)
    assert prompt.content == "original"


@pytest.mark.parametrize("error_type", [RuntimeError, httpx2.ReadError, UnexpectedModelBehavior])
async def test_stream_failure_resumes_with_partial_history_and_shared_usage(error_type: type[Exception]) -> None:
    calls: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        calls.append(deepcopy(messages))
        if len(calls) == 1:
            yield "partial answer"
            raise error_type("stream disconnected")
        yield "resumed answer"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(),
    )

    result = await executable.run("start", bindings=RunBindings.embedded())

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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(),
    )

    result = await executable.run("start", bindings=RunBindings.embedded())

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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    result = await executable.run("start", bindings=RunBindings.embedded())

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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(),
    )

    result = await executable.run("start", bindings=RunBindings.embedded())

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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(tools=[lookup], id="test-tools"),),
        model_recovery=_recovery_policy(max_attempts=2),
    )

    result = await executable.run("start", bindings=RunBindings.embedded())

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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    result = await executable.run(
        "start",
        bindings=RunBindings.embedded(),
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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    result = await executable.run("start", bindings=RunBindings.embedded())

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
async def test_malformed_native_tool_pairs_preserve_surrounding_text(
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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    result = await executable.run("start", bindings=RunBindings.embedded())

    assert result.status == "failed"
    assert not any(
        isinstance(part, NativeToolCallPart | NativeToolReturnPart)
        for message in result.all_messages()
        if isinstance(message, ModelResponse)
        for part in message.parts
    )
    assert result.state is not None
    assert result.state.message_history == result.all_messages()
    tail = result.state.message_history[-1]
    assert isinstance(tail, ModelResponse)
    assert tail.state == "interrupted"
    assert tail.parts == [TextPart(content="partial answer")]


async def test_unmatched_native_tool_call_preserves_surrounding_text() -> None:
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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    result = await executable.run("start", bindings=RunBindings.embedded())

    assert result.status == "failed"
    assert not any(
        isinstance(part, NativeToolCallPart)
        for message in result.all_messages()
        if isinstance(message, ModelResponse)
        for part in message.parts
    )
    assert result.state is not None
    assert result.state.message_history == result.all_messages()
    tail = result.state.message_history[-1]
    assert isinstance(tail, ModelResponse)
    assert tail.state == "interrupted"
    assert tail.parts == [TextPart(content="partial answer after native call")]


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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(),
    )

    result = await executable.run("start", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "resumed answer"
    assert len(calls) == 2
    assert not any(
        isinstance(part, NativeToolCallPart)
        for message in calls[1]
        if isinstance(message, ModelResponse)
        for part in message.parts
    )


async def test_unfinalized_tool_call_without_text_leaves_no_partial_response() -> None:
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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(),
    )

    result = await executable.run("start", bindings=RunBindings.embedded())

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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    result = await executable.run("start", bindings=RunBindings.embedded())

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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(),
    )

    result = await executable.run("start", bindings=RunBindings.embedded())

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

    executable = HarnessBuilder().build(
        AgentSpec(),
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

    result = await executable.run("start", bindings=RunBindings.embedded())

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

    monkeypatch.setattr("a13n_harness.recovery.random.uniform", use_ceiling)
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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(max_attempts=3),
    )

    events = []
    async with executable.stream("start", bindings=RunBindings.embedded()) as run:
        async for item in run:
            if isinstance(item, HarnessEvent):
                events.append(item)
        result = run.result

    notices = [
        item.event.payload
        for item in events
        if isinstance(item.event, HarnessExtensionEvent) and item.event.kind == "recovery"
    ]
    assert notices == [
        {"type": "model_retry_scheduled", "attempt": 2, "max_attempts": 3, "delay_seconds": 0.0},
        {"type": "model_retry_scheduled", "attempt": 3, "max_attempts": 3, "delay_seconds": 0.0},
    ]
    assert len({item.run_id for item in events}) == 1
    assert [item.sequence for item in events] == sorted({item.sequence for item in events})
    assert calls == 3
    assert result is not None
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "model_recovery_exhausted"
    assert result.failure.retry_hint == "new_run"
    assert "after 3 attempts" in result.failure.message
    assert result.usage.requests == 3


async def test_usage_limit_never_enters_model_recovery() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages, info
        calls += 1
        yield "unreachable"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(max_attempts=5),
    )

    result = await executable.run(
        "start",
        bindings=RunBindings.embedded(),
        usage_limits=UsageLimits(request_limit=0),
    )

    assert calls == 0
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "usage_limit_exceeded"


async def test_cancel_after_retry_notice_does_not_start_the_scheduled_attempt() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        calls += 1
        yield "partial"
        raise httpx2.ReadError("connection reset")

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=ModelRecoveryPolicy(
            enabled=True,
            backoff_initial_seconds=60,
            backoff_max_seconds=60,
        ),
    )
    notices = 0
    async with executable.stream("start", bindings=RunBindings.embedded()) as run:
        async with asyncio.timeout(2):
            async for item in run:
                if (
                    isinstance(item, HarnessEvent)
                    and isinstance(item.event, HarnessExtensionEvent)
                    and item.event.kind == "recovery"
                ):
                    notices += 1
                    run.cancel()
        result = run.result
    assert notices == 1
    assert calls == 1
    assert result is not None and result.status == "cancelled"


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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=ModelRecoveryPolicy(
            enabled=True,
            max_attempts=5,
            backoff_initial_seconds=60,
            backoff_max_seconds=60,
        ),
    )

    async with executable.stream("start", bindings=RunBindings.embedded()) as run_stream:
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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    async with executable.stream("start", bindings=RunBindings.embedded()) as run_stream:
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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(max_attempts=5),
    )

    async with executable.stream("start", bindings=RunBindings.embedded()) as run_stream:
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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=SuspendingModel(FunctionModel(stream_function=stream)),
        model_recovery=_recovery_policy(max_attempts=5),
    )

    result = await executable.run("start", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "first second"
    assert calls == 2
    responses = [message for message in result.all_messages() if isinstance(message, ModelResponse)]
    assert len(responses) == 1
    assert responses[0].state == "complete"


@pytest.mark.parametrize(
    ("message", "recoverable"),
    [
        ("Tool 'some_tool' exceeded max retries count of 1.", False),
        ("Exceeded maximum retries (1) for output validation", False),
        ("Stream ended unexpectedly", True),
    ],
)
def test_unexpected_model_behavior_retry_classification(message: str, recoverable: bool) -> None:
    assert is_recoverable_model_failure(UnexpectedModelBehavior(message), ()) is recoverable


@pytest.mark.parametrize("retries", [0, 1, 2])
@pytest.mark.parametrize("invalid_args", [False, True], ids=["model-retry", "argument-validation"])
async def test_tool_retry_exhaustion_does_not_start_a_new_attempt(retries: int, invalid_args: bool) -> None:
    model_calls = 0
    tool_calls = 0

    def failing_tool(value: int) -> str:
        nonlocal tool_calls
        tool_calls += 1
        raise ModelRetry("Try a different value")

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[dict[int, DeltaToolCall]]:
        nonlocal model_calls
        del messages, info
        model_calls += 1
        yield {
            0: DeltaToolCall(
                name="failing_tool",
                json_args='{"value": "invalid"}' if invalid_args else '{"value": 1}',
                tool_call_id=f"tool-{model_calls}",
            )
        }

    executable = HarnessBuilder().build(
        AgentSpec(retries={"tools": retries}),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(tools=[failing_tool], id="test-tools"),),
        model_recovery=_recovery_policy(max_attempts=5),
    )

    async with executable.stream("start", bindings=RunBindings.embedded()) as run_stream:
        events = [event async for event in run_stream]
        result = run_stream.result

    assert model_calls == retries + 1
    assert tool_calls == (0 if invalid_args else retries + 1)
    assert result is not None and result.status == "failed"
    assert result.usage.requests == retries + 1
    assert result.failure is not None
    assert result.failure.code == "agent_run_failed"
    assert not any(
        isinstance(event, HarnessEvent)
        and isinstance(event.event, HarnessExtensionEvent)
        and event.event.kind == "recovery"
        for event in events
    )
    attempt_ids = {message.run_id for message in result.all_messages() if isinstance(message, ModelResponse)}
    assert len(attempt_ids) == 1


@pytest.mark.parametrize("retries", [0, 1, 2])
async def test_output_retry_exhaustion_does_not_start_a_new_attempt(retries: int) -> None:
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

    executable = HarnessBuilder().build(
        AgentSpec(retries={"output": retries}),
        output_type=ToolOutput(RequiredOutput, name="finish"),
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(max_attempts=5),
    )

    result = await executable.run("start", bindings=RunBindings.embedded())

    assert calls == retries + 1
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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(tools=[failing_tool], id="test-tools"),),
        model_recovery=_recovery_policy(max_attempts=5),
    )

    with pytest.raises(RuntimeError, match="tool failed"):
        await executable.run("start", bindings=RunBindings.embedded())

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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(tools=[deferred_tool], id="test-tools"),),
        model_recovery=_recovery_policy(max_attempts=5),
    )

    result = await executable.run("start", bindings=RunBindings.embedded())

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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    result = await executable.run(
        "continue",
        bindings=RunBindings.embedded(),
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


@pytest.mark.parametrize("tool_recovery", ["never", "always", "declared"])
async def test_saved_interrupted_tool_history_is_repaired_before_rerun(tool_recovery: str) -> None:
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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    result = await executable.run(
        "continue",
        bindings=RunBindings.embedded(),
        previous_state=HarnessState.new(message_history=history),
        tool_recovery=tool_recovery,
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


@pytest.mark.parametrize("entrypoint", ["run", "stream"])
@pytest.mark.parametrize("tool_recovery", [None, "never", "always"])
async def test_unmarked_restored_tool_calls_require_execution_opt_in(
    entrypoint: str, tool_recovery: str | None
) -> None:
    executions: list[int] = []
    model_history: list[list[ModelMessage]] = []

    def change(value: int) -> int:
        executions.append(value)
        return value

    async def model_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        model_history.append(deepcopy(messages))
        yield "continued"

    executable = HarnessBuilder(instrumentation=None).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model_stream),
        capabilities=[Capability(id="test-tools", tools=[change])],
    )
    state = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart(content="Change both values")]),
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name="change", args={"value": value}, tool_call_id=f"call-{value}")
                    for value in (1, 2)
                ]
            ),
        ),
    )
    payload = state.model_dump_json()
    restored = HarnessState.model_validate_json(payload)
    options = {} if tool_recovery is None else {"tool_recovery": tool_recovery}
    if entrypoint == "run":
        result = await executable.run(previous_state=restored, **options)
    else:
        result = None
        async with executable.stream(previous_state=restored, **options) as stream:
            async for event in stream:
                if isinstance(event, HarnessRunResultEvent):
                    result = event.result
        assert result is not None

    assert result.output_or_raise() == "continued"
    assert len(model_history) == 1
    returns = {
        part.tool_call_id: part
        for message in model_history[0]
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }
    assert set(returns) == {"call-1", "call-2"}
    if tool_recovery == "always":
        assert sorted(executions) == [1, 2]
        assert [returns[f"call-{value}"].content for value in (1, 2)] == [1, 2]
    else:
        assert executions == []
        assert all(part.outcome == "failed" and part.content == INTERRUPTED_TOOL_RESULT for part in returns.values())
    assert restored.model_dump_json() == payload


@pytest.mark.parametrize("input", [None, "Continue"])
@pytest.mark.parametrize("tool_recovery", ["never", "declared"])
async def test_restored_partial_tool_results_preserve_completed_work(input: str | None, tool_recovery) -> None:
    executions: list[str] = []
    model_history: list[list[ModelMessage]] = []

    def change() -> str:
        executions.append("executed")
        return "changed"

    async def model_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        model_history.append(deepcopy(messages))
        yield "continued"

    executable = HarnessBuilder(instrumentation=None).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model_stream),
        capabilities=[Capability(id="test-tools", tools=[change])],
    )
    history = (
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="change", args={}, tool_call_id="done"),
                ToolCallPart(tool_name="change", args={}, tool_call_id="unknown"),
            ]
        ),
        ModelRequest(parts=[ToolReturnPart(tool_name="change", tool_call_id="done", content="saved")]),
    )
    result = await executable.run(
        input, previous_state=HarnessState.new(message_history=history), tool_recovery=tool_recovery
    )

    assert result.output_or_raise() == "continued"
    assert executions == []
    returns = [part for message in model_history[0] for part in message.parts if isinstance(part, ToolReturnPart)]
    assert [(part.tool_call_id, part.content) for part in returns] == [
        ("done", "saved"),
        ("unknown", INTERRUPTED_TOOL_RESULT),
    ]


def test_restored_provider_suspension_is_not_closed_as_unknown() -> None:
    history = (
        ModelResponse(
            parts=[ToolCallPart(tool_name="change", args={}, tool_call_id="provider-pending")],
            state="suspended",
        ),
    )
    normalized, closed = normalize_interrupted_history(history, close_pending_tools=True)
    assert normalized == history
    assert closed == 0


@pytest.mark.parametrize("entrypoint", ["run", "stream"])
@pytest.mark.parametrize("mode", ["never", "always", "declared"])
async def test_declared_recovery_selects_marked_calls_and_allows_fresh_calls(entrypoint: str, mode) -> None:
    executions: list[str] = []
    histories: list[list[ModelMessage]] = []

    def lookup() -> str:
        executions.append("lookup")
        return "current value"

    def send() -> str:
        executions.append("send")
        return "sent"

    async def model_stream(messages, info) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        histories.append(deepcopy(messages))
        if len(histories) == 1:
            yield {0: DeltaToolCall(name="send", json_args="{}", tool_call_id="fresh-write")}
        else:
            yield "done"

    executable = HarnessBuilder(instrumentation=None).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model_stream),
        capabilities=[
            Capability(id="test.lookup", tools=[lookup]),
            Capability(id="test.send", tools=[send]),
            SetToolMetadata(
                tools=lambda ctx, tool: tool.capability_id == "test.lookup",
                **{RECOVERY_RETRY_SAFE_METADATA_KEY: True},
            ),
        ],
    )
    state = HarnessState.new(
        message_history=(
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name="lookup", args={}, tool_call_id="old-read"),
                    ToolCallPart(tool_name="send", args={}, tool_call_id="old-write"),
                ]
            ),
        )
    )
    payload = state.model_dump_json()
    restored = HarnessState.model_validate_json(payload)
    if entrypoint == "run":
        result = await executable.run(previous_state=restored, tool_recovery=mode)
    else:
        result = None
        async with executable.stream(previous_state=restored, tool_recovery=mode) as stream:
            async for event in stream:
                if isinstance(event, HarnessRunResultEvent):
                    result = event.result
        assert result is not None
    assert result.output_or_raise() == "done"
    if mode == "never":
        assert executions == ["send"]
    elif mode == "always":
        assert sorted(executions) == ["lookup", "send", "send"]
    else:
        assert executions == ["lookup", "send"]
    returns = {p.tool_call_id: p for m in histories[0] for p in m.parts if isinstance(p, ToolReturnPart)}
    assert returns["old-read"].content == (INTERRUPTED_TOOL_RESULT if mode == "never" else "current value")
    assert returns["old-write"].content == ("sent" if mode == "always" else INTERRUPTED_TOOL_RESULT)
    if mode != "always":
        assert returns["old-write"].outcome == "failed"
    assert restored.model_dump_json() == payload


@pytest.mark.parametrize("marker", [None, False, "true", True])
async def test_declared_recovery_does_not_treat_retry_safety_as_approval(marker: object) -> None:
    executions: list[str] = []
    histories: list[list[ModelMessage]] = []

    def guarded() -> str:
        executions.append("executed")
        return "done"

    async def model_stream(messages, info) -> AsyncIterator[str]:
        histories.append(deepcopy(messages))
        yield "continued"

    executable = HarnessBuilder(instrumentation=None).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model_stream),
        capabilities=[
            Capability(
                id="test.guarded",
                tools=[
                    Tool(
                        guarded,
                        requires_approval=True,
                        metadata={RECOVERY_RETRY_SAFE_METADATA_KEY: marker},
                    )
                ],
            )
        ],
    )
    state = HarnessState.new(
        message_history=(
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name="guarded", args={}, tool_call_id="old-call"),
                ]
            ),
        )
    )
    result = await executable.run(previous_state=state, tool_recovery="declared")
    assert executions == []
    if marker is True:
        assert result.status == "suspended"
        assert result.deferred is not None
        assert [call.tool_call_id for call in result.deferred.approvals] == ["old-call"]
        assert histories == []
    else:
        assert result.output_or_raise() == "continued"
        returns = [p for m in histories[0] for p in m.parts if isinstance(p, ToolReturnPart)]
        assert returns[0].content == INTERRUPTED_TOOL_RESULT


async def test_declared_recovery_closes_unmarked_call_before_argument_validation() -> None:
    def change(value: int) -> int:
        pytest.fail("An unmarked restored call must not execute")

    histories: list[list[ModelMessage]] = []

    async def model_stream(messages, info) -> AsyncIterator[str]:
        histories.append(deepcopy(messages))
        yield "continued"

    executable = HarnessBuilder(instrumentation=None).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model_stream),
        capabilities=[Capability(id="test.change", tools=[change])],
    )
    result = await executable.run(
        tool_recovery="declared",
        previous_state=HarnessState.new(
            message_history=(
                ModelResponse(
                    parts=[
                        ToolCallPart(tool_name="change", args={"value": "invalid"}, tool_call_id="old-call"),
                    ]
                ),
            )
        ),
    )
    assert result.output_or_raise() == "continued"
    returns = [p for m in histories[0] for p in m.parts if isinstance(p, ToolReturnPart)]
    assert returns[0].content == INTERRUPTED_TOOL_RESULT


@pytest.mark.parametrize("native_tool", [False, True])
@pytest.mark.parametrize("max_attempts", [2, 3])
async def test_retry_retains_visible_text_beside_an_unfinished_tool(native_tool: bool, max_attempts: int) -> None:
    calls: list[list[ModelMessage]] = []

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall | NativeToolCallPart]]:
        del info
        calls.append(deepcopy(messages))
        if len(calls) < max_attempts:
            yield f"Inspection {len(calls)} completed. "
            yield "Next I will inspect another file."
            if native_tool:
                yield {
                    1: NativeToolCallPart(tool_name="web_search", args={"query": "next step"}, tool_call_id="native-1")
                }
            else:
                yield {1: DeltaToolCall(name="inspect", json_args='{"path":', tool_call_id="tool-1")}
            raise RuntimeError("stream disconnected during tool call")
        yield "Continued without repeating the first inspection."

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(max_attempts=max_attempts),
    )
    result = await executable.run("start", bindings=RunBindings.embedded())

    assert result.status == "completed"
    assert len(calls) == max_attempts
    for attempt_index, history in enumerate(calls[1:], start=1):
        recovered = [message for message in history if isinstance(message, ModelResponse)]
        assert len(recovered) == attempt_index
        for index, response in enumerate(recovered, start=1):
            assert response.state == "interrupted"
            assert response.parts == [
                TextPart(content=f"Inspection {index} completed. Next I will inspect another file.")
            ]
        assert not any(isinstance(part, ToolReturnPart) for message in history for part in message.parts)
    assert result.state is not None
    assert result.state.message_history == result.all_messages()
    assert [message for message in result.state.message_history if isinstance(message, ModelResponse)][:-1] == recovered


@pytest.mark.parametrize("native_tool", [False, True])
@pytest.mark.parametrize("stop", ["cancel", "exhaust"])
async def test_stopped_mixed_stream_exports_partial_text(native_tool: bool, stop: str) -> None:
    started = asyncio.Event()
    calls = 0

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall | NativeToolCallPart]]:
        nonlocal calls
        del messages, info
        calls += 1
        yield "Already inspected. "
        yield "Next step pending."
        if native_tool:
            yield {1: NativeToolCallPart(tool_name="web_search", args={}, tool_call_id="native-1")}
        else:
            yield {1: DeltaToolCall(name="inspect", json_args='{"path":', tool_call_id="tool-1")}
        started.set()
        if stop == "cancel":
            await asyncio.Event().wait()
        raise RuntimeError("stream disconnected")

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=_recovery_policy(max_attempts=1),
    )
    async with executable.stream("start", bindings=RunBindings.embedded()) as run_stream:

        async def consume():
            return [event async for event in run_stream][-1]

        pending = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), timeout=2)
        if stop == "cancel":
            run_stream.cancel()
        terminal = await asyncio.wait_for(pending, timeout=2)

    assert isinstance(terminal, HarnessRunResultEvent)
    assert terminal.result.status == ("cancelled" if stop == "cancel" else "failed")
    assert calls == 1
    state = await run_stream.export_state()
    assert state.message_history == terminal.result.all_messages()
    assert terminal.result.state is not None
    assert state.message_history == terminal.result.state.message_history
    tail = state.message_history[-1]
    assert isinstance(tail, ModelResponse)
    assert tail.state == "interrupted"
    assert tail.parts == [TextPart(content="Already inspected. Next step pending.")]


@pytest.mark.parametrize("observed", [False, True])
@pytest.mark.parametrize("close_tool_calls", [False, True])
def test_native_filter_preserves_finalized_parts_and_closes_only_complete_ordinary_calls(
    observed: bool, close_tool_calls: bool
) -> None:
    response = ModelResponse(
        parts=[
            ThinkingPart(content="finished reasoning", signature="signed"),
            NativeToolCallPart(tool_name="web_search", args={}, tool_call_id="native-complete"),
            NativeToolReturnPart(tool_name="web_search", content="found", tool_call_id="native-complete"),
            ToolCallPart(tool_name="inspect", args={"path": "first"}, tool_call_id="ordinary-complete"),
            TextPart(content="Inspection completed. Next step pending."),
            NativeToolCallPart(tool_name="web_search", args={}, tool_call_id="native-unmatched"),
        ],
        state="interrupted",
        model_name="test-model",
        provider_name="test-provider",
        provider_response_id="response-1",
        metadata={"original": True},
    )
    tracker = None
    if observed:
        tracker = InterruptedResponseTracker()
        for index, part in enumerate(response.parts):
            tracker.observe(PartEndEvent(index=index, part=part), response_history_count=0)
        # Unfinished tool arguments must not erase finalized parts or receive a synthetic result.
        tracker.observe(
            PartStartEvent(
                index=len(response.parts),
                part=ToolCallPart(tool_name="inspect", args='{"path":', tool_call_id="unfinished"),
            ),
            response_history_count=0,
        )
    original = deepcopy(response)

    normalized, closed = normalize_interrupted_history(
        (response,), response_tracker=tracker, close_tool_calls=close_tool_calls
    )

    assert closed == int(close_tool_calls)
    retained = normalized[0]
    assert isinstance(retained, ModelResponse)
    expected = deepcopy(response)
    expected.parts = [response.parts[index] for index in (0, 3, 4)]
    assert retained == expected
    assert response == original
    if close_tool_calls:
        assert len(normalized) == 2
        assert len(normalized[1].parts) == 1
        result = normalized[1].parts[0]
        assert isinstance(result, ToolReturnPart)
        assert result.tool_name == "inspect"
        assert result.tool_call_id == "ordinary-complete"
        assert result.content == INTERRUPTED_TOOL_RESULT
        assert result.outcome == "failed"
    else:
        assert len(normalized) == 1
    assert normalize_interrupted_history(normalized, close_tool_calls=close_tool_calls) == (normalized, 0)
