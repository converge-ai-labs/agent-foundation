from __future__ import annotations

import ssl
from collections.abc import AsyncIterator, Sequence

import httpx2
import pytest
from a13n_harness import (
    AgentContext,
    DeferredToolResume,
    HarnessBuilder,
    HarnessEvent,
    ModelRecoveryPolicy,
    RunBindings,
)
from a13n_harness.capabilities.lifecycle import LifecycleEventCapability
from a13n_harness.events import HarnessExtensionEvent
from a13n_harness.recovery import is_recoverable_model_failure
from pydantic_ai import Agent, RunContext
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability, Capability
from pydantic_ai.exceptions import (
    ContentFilterError,
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
)
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolReturnPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import ExternalToolset

pytestmark = pytest.mark.anyio


async def test_success_replenishes_budget_and_resets_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    tools = 0
    delays: list[int] = []
    prompts: list[int] = []

    def delay(self: ModelRecoveryPolicy, index: int) -> float:
        delays.append(index)
        return 0

    def prompt(error: BaseException, index: int, messages: Sequence[ModelMessage]) -> str:
        prompts.append(index)
        return "continue"

    def progress() -> str:
        nonlocal tools
        tools += 1
        return "done"

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        nonlocal calls
        calls += 1
        if calls % 3:
            yield "partial"
            raise httpx2.ReadError("disconnected")
        if calls < 9:
            yield {0: DeltaToolCall(name="progress", json_args="{}", tool_call_id=f"call-{calls}")}
        else:
            yield "finished"

    monkeypatch.setattr(ModelRecoveryPolicy, "delay", delay)
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(tools=[progress], id="progress"),),
        model_recovery=ModelRecoveryPolicy(enabled=True, max_attempts=3, prompt_factory=prompt),
    )
    events = []
    async with executable.stream("start") as run:
        async for event in run:
            if isinstance(event, HarnessEvent):
                events.append(event)
        result = run.result
    assert result is not None
    notices = [
        event.event.payload["attempt"]
        for event in events
        if isinstance(event.event, HarnessExtensionEvent) and event.event.kind == "recovery"
    ]
    assert notices == [2, 3, 2, 3, 2, 3]
    assert [event.sequence for event in events] == sorted({event.sequence for event in events})

    assert result.output_or_raise() == "finished"
    assert calls == result.usage.requests == 9
    assert tools == 2
    assert delays == prompts == [1, 2, 1, 2, 1, 2]
    assert len({message.run_id for message in result.all_messages() if isinstance(message, ModelResponse)}) == 7


async def test_auxiliary_success_does_not_replenish_primary_budget() -> None:
    auxiliary_calls = 0
    primary_calls = 0

    def auxiliary_response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal auxiliary_calls
        auxiliary_calls += 1
        return ModelResponse(parts=[TextPart("summary")])

    auxiliary = Agent(
        model=FunctionModel(auxiliary_response),
        deps_type=AgentContext,
        capabilities=[LifecycleEventCapability()],
    )

    class AuxiliaryRequest(AbstractCapability[AgentContext]):
        async def before_model_request(
            self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
        ) -> ModelRequestContext:
            # Like compaction, use a different native invocation with shared deps
            # and a non-streamed successful response before the primary request.
            result = await auxiliary.run("summarize", deps=ctx.deps)
            assert result.output == "summary"
            return request_context

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal primary_calls
        primary_calls += 1
        raise httpx2.ConnectError("unavailable")
        yield "unreachable"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(AuxiliaryRequest(),),
        model_recovery=ModelRecoveryPolicy(enabled=True, max_attempts=3, backoff_initial_seconds=0),
    )
    result = await executable.run("start")
    assert result.failure is not None
    assert result.failure.code == "model_recovery_exhausted"
    assert primary_calls == auxiliary_calls == 3


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, 529])
def test_transient_http_status_is_recoverable(status: int) -> None:
    assert is_recoverable_model_failure(ModelHTTPError(status, "test"))


@pytest.mark.parametrize("status", [400, 401, 501, 505])
def test_other_http_status_is_terminal(status: int) -> None:
    assert not is_recoverable_model_failure(ModelHTTPError(status, "test"))


# Every recognized code once, and every placement of the code field at least once.
@pytest.mark.parametrize(
    ("nested", "field", "code"),
    [
        (True, "code", "insufficient_quota"),
        (False, "code", "insufficient_quota"),
        (False, "type", "billing_hard_limit_reached"),
        (True, "type", "usage_limit_reached"),
    ],
)
def test_permanent_quota_code_overrides_retryable_status(nested: bool, field: str, code: str) -> None:
    body = {"error": {field: code}} if nested else {field: code}
    assert not is_recoverable_model_failure(ModelHTTPError(429, "test", body))


@pytest.mark.parametrize(
    "error",
    [
        httpx2.ConnectError("offline"),
        httpx2.ReadTimeout("timeout"),
        httpx2.RemoteProtocolError("truncated"),
        ConnectionResetError("reset"),
        TimeoutError("timeout"),
    ],
)
def test_network_error_remains_recoverable_through_provider_cause(error: Exception) -> None:
    wrapper = ModelAPIError("test", "provider request failed")
    sdk_error = Exception("SDK wrapper")
    sdk_error.__cause__ = error
    wrapper.__cause__ = sdk_error
    assert is_recoverable_model_failure(error)
    assert is_recoverable_model_failure(wrapper)


def test_certificate_verification_error_is_not_retried() -> None:
    error = httpx2.ConnectError("TLS failed")
    error.__cause__ = ssl.SSLCertVerificationError("invalid certificate")
    assert not is_recoverable_model_failure(error)


@pytest.mark.parametrize(
    "error",
    [
        ModelAPIError("test", "Streamed response ended without a `finish_reason`"),
        UnexpectedModelBehavior("Streamed response ended without content or tool calls"),
    ],
)
def test_known_upstream_stream_termination_errors_are_recoverable(error: Exception) -> None:
    assert is_recoverable_model_failure(error)


def test_unknown_provider_cause_is_terminal() -> None:
    error = ModelAPIError("test", "SDK connection error")
    error.__cause__ = ValueError("invalid configuration")
    assert not is_recoverable_model_failure(error)


def test_unknown_cyclic_provider_cause_is_terminal() -> None:
    error = ModelAPIError("test", "unknown")
    error.__cause__ = error
    assert not is_recoverable_model_failure(error)


@pytest.mark.parametrize(
    "error",
    [
        ModelHTTPError(401, "test"),
        ContentFilterError("filtered"),
        RuntimeError("programming error"),
        httpx2.LocalProtocolError("invalid headers"),
    ],
)
async def test_permanent_and_unknown_model_failures_stop_without_retry(error: Exception) -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        calls += 1
        raise error
        yield "unreachable"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=ModelRecoveryPolicy(enabled=True, backoff_initial_seconds=0),
    )
    events = []
    async with executable.stream("start", bindings=RunBindings.embedded()) as run:
        async for event in run:
            if isinstance(event, HarnessEvent):
                events.append(event)
        result = run.result
    assert result is not None and result.failure is not None
    assert result.failure.code == "agent_run_failed"
    assert calls == 1
    assert not any(
        isinstance(event.event, HarnessExtensionEvent) and event.event.kind == "recovery" for event in events
    )


@pytest.mark.parametrize("error", [httpx2.ReadError("tool network failure"), ModelHTTPError(503, "tool")])
async def test_transient_errors_from_tools_do_not_restart_model_execution(error: Exception) -> None:
    calls = 0

    def fail() -> str:
        raise error

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[dict[int, DeltaToolCall]]:
        nonlocal calls
        calls += 1
        yield {0: DeltaToolCall(name="fail", json_args="{}", tool_call_id="call-1")}

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(tools=[fail], id="fail"),),
        model_recovery=ModelRecoveryPolicy(enabled=True, backoff_initial_seconds=0),
    )
    if isinstance(error, ModelHTTPError):
        result = await executable.run("start")
        assert result.failure is not None
        assert result.failure.code == "agent_run_failed"
    else:
        with pytest.raises(httpx2.ReadError):
            await executable.run("start")
    assert calls == 1


async def test_budget_reset_does_not_reinject_deferred_results() -> None:
    calls = 0
    tools = 0

    external = ExternalToolset(
        [ToolDefinition(name="external", parameters_json_schema={"type": "object", "properties": {}})]
    )

    def progress() -> str:
        nonlocal tools
        tools += 1
        return "progress"

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {0: DeltaToolCall(name="external", json_args="{}", tool_call_id="external-1")}
        elif calls in (2, 4):
            raise httpx2.ReadError("disconnected")
        elif calls == 3:
            yield {0: DeltaToolCall(name="progress", json_args="{}", tool_call_id="progress-1")}
        else:
            yield "finished"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(tools=[progress], toolsets=[external], id="tools"),),
        model_recovery=ModelRecoveryPolicy(enabled=True, max_attempts=2, backoff_initial_seconds=0),
    )
    first = await executable.run("start")
    assert first.deferred is not None and first.state is not None
    result = await executable.run(
        previous_state=first.state,
        deferred_resume=DeferredToolResume(
            first.deferred,
            first.deferred.build_results(calls={"external-1": "external result"}),
        ),
    )
    assert result.output_or_raise() == "finished"
    assert calls == 5 and tools == 1
    results = [
        part
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_call_id == "external-1"
    ]
    assert len(results) == 1
    assert results[0].content == "external result"
