from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy

import pytest
from a13n_harness import ContextRecoveryPolicy, HarnessBuilder, RunBindings
from a13n_harness._context_recovery import DEFAULT_RECOVERY_CHARS, WARNING, mark_input, reconstruct
from a13n_harness.capabilities.context import CompactionCapability
from a13n_harness.content import content_items, input_request, merge_request_history
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.exceptions import ModelContextWindowExceededError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel


def test_reconstruction_preserves_order_roles_and_merged_provenance() -> None:
    messages = [
        ModelRequest(parts=[SystemPromptPart("standing system")], instructions="standing instructions"),
        input_request(mark_input(content_items("old task"), kind="task", run_id="old")),
        ModelResponse(parts=[TextPart("choose option two")]),
        input_request(mark_input(content_items("do option two"), kind="task", run_id="new")),
        input_request(mark_input(content_items("do not publish"), kind="steer", run_id="new")),
        ModelResponse(parts=[ToolCallPart("shell", {"command": "test"}, tool_call_id="call-1")]),
        ModelRequest(
            parts=[ToolReturnPart("shell", {"execution_id": "exec-1", "path": "/result.txt"}, tool_call_id="call-1")]
        ),
    ]
    rebuilt = reconstruct(merge_request_history(messages))
    prompt = rebuilt.prompt
    assert prompt.index("old task") < prompt.index("Immediately preceding assistant reference")
    assert (
        prompt.index("Immediately preceding assistant reference")
        < prompt.index("do option two")
        < prompt.index("do not publish")
    )
    assert "exec-1" in prompt and "/result.txt" in prompt
    assert WARNING in prompt
    request = rebuilt.messages[0]
    assert isinstance(request, ModelRequest)
    assert isinstance(request.parts[0], SystemPromptPart)
    assert request.parts[0].content == "standing system"
    assert not any(isinstance(part, ToolCallPart | ToolReturnPart) for part in request.parts)


@pytest.mark.parametrize("budget", [2048, 4096, DEFAULT_RECOVERY_CHARS])
def test_reconstruction_total_budget_and_huge_inputs(budget: int) -> None:
    messages = [
        input_request(mark_input(content_items("task " * 100_000), kind="task", run_id="run")),
        input_request(mark_input(content_items("correction " * 10_000), kind="steer", run_id="run")),
        *[ModelResponse(parts=[TextPart(f"record {i} " + "x" * 500)]) for i in range(500)],
    ]
    first = reconstruct(messages, max_chars=budget)
    second = reconstruct(messages, max_chars=budget)
    assert first.prompt == second.prompt
    assert len(first.prompt) <= budget
    assert "task " in first.prompt and "correction " in first.prompt
    assert first.omitted > 0
    assert "could not be retained" in first.prompt
    assert "Immediate root-cause investigation" in first.prompt


@pytest.mark.anyio
@pytest.mark.parametrize("proactive", [False, True])
async def test_primary_then_summary_overflow_resumes_without_transient_retry(proactive: bool) -> None:
    calls: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        calls.append(deepcopy(messages))
        if len(calls) <= 2:
            raise ModelContextWindowExceededError("test", "too large")
        yield "resumed"

    async def summary(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(deepcopy(messages))
        raise ModelContextWindowExceededError("test", "too large")

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(function=summary, stream_function=stream),
        capabilities=[CompactionCapability()] if proactive else [],
    )
    result = await executable.run("finish the task", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "resumed"
    assert len(calls) == 3
    assert "Latest task-start input" in str(calls[-1])
    assert "finish the task" in str(calls[-1])
    assert result.usage.requests == 3


@pytest.mark.anyio
async def test_second_overflow_is_terminal_and_allowance_survives_host_takeover() -> None:
    from a13n_harness._context_recovery import CONTEXT_RECOVERY_STATE_ID

    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        calls += 1
        raise ModelContextWindowExceededError("test", "private provider text")
        yield "unreachable"

    async def summary(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        raise ModelContextWindowExceededError("test", "private provider text")

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(function=summary, stream_function=stream),
        capabilities=[CompactionCapability()],
    )
    bindings = RunBindings.embedded(context_recovery_scope_id="host-logical-run")
    first = await executable.run("huge task", bindings=bindings)
    assert first.failure is not None and first.failure.code == "context_recovery_exhausted"
    assert calls == 3
    assert first.state.agent_context_state.get(CONTEXT_RECOVERY_STATE_ID) is not None
    second = await executable.run(None, previous_state=first.state, bindings=bindings)
    assert second.failure is not None and second.failure.code == "context_recovery_exhausted"
    assert calls == 4  # The consumed allowance prevents another summary or reconstruction.


@pytest.mark.anyio
@pytest.mark.parametrize("enabled", [True, False])
async def test_context_recovery_requires_native_summary_overflow_and_enabled_policy(enabled: bool) -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("not a context overflow")
        raise ModelContextWindowExceededError("test", "too large")
        yield "unreachable"

    async def summary(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        raise ValueError("not a context overflow")

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(function=summary, stream_function=stream),
        context_recovery=ContextRecoveryPolicy(enabled=enabled),
    )
    result = await executable.run("task", bindings=RunBindings.embedded())
    assert result.status == "failed"
    assert calls == (2 if enabled else 1)
    assert "Context recovery" not in str(result.state)


@pytest.mark.anyio
@pytest.mark.parametrize("primary_overflows", [False, True])
async def test_summary_first_failure_is_reused_only_when_primary_overflows(primary_overflows: bool) -> None:
    from a13n_harness import HarnessState
    from a13n_harness._context_recovery import CONTEXT_RECOVERY_STATE_ID
    from a13n_harness.capabilities.context import _COMPACTION_PROMPT, CompactionPolicy
    from pydantic_ai.messages import UserPromptPart
    from pydantic_ai.usage import RequestUsage

    calls: list[str] = []

    async def stream(messages, info):
        is_summary = any(
            isinstance(p, UserPromptPart) and p.content == _COMPACTION_PROMPT for m in messages for p in m.parts
        )
        calls.append("summary" if is_summary else "primary")
        if is_summary or (primary_overflows and calls == ["summary", "primary"]):
            raise ModelContextWindowExceededError("test", "too large")
        yield "done"

    previous = HarnessState.new(
        message_history=[
            ModelRequest(parts=[UserPromptPart("old task")]),
            ModelResponse(parts=[TextPart("old answer")], usage=RequestUsage(input_tokens=9000)),
        ]
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=[CompactionCapability(CompactionPolicy(trigger_tokens=1000))],
    )
    result = await executable.run("continue", previous_state=previous)
    assert result.output_or_raise() == "done"
    assert calls == (["summary", "primary", "primary"] if primary_overflows else ["summary", "primary"])
    assert (result.state.agent_context_state.get(CONTEXT_RECOVERY_STATE_ID) is not None) is primary_overflows


@pytest.mark.anyio
async def test_completed_tools_are_not_replayed_and_detachment_survives_next_run() -> None:
    from contextlib import asynccontextmanager

    from a13n_harness._context_recovery import CONTEXT_RECOVERY_STATE_ID
    from pydantic_ai.capabilities import Capability
    from pydantic_ai.models.function import DeltaToolCall

    calls = []
    executions = []

    async def work():
        executions.append("work")
        return "completed side effect"

    class RecordingModel(FunctionModel):
        @asynccontextmanager
        async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
            calls.append((deepcopy(messages), deepcopy(model_settings), model_request_parameters))
            async with super().request_stream(
                messages, model_settings, model_request_parameters, run_context
            ) as response:
                yield response

    async def stream(messages, info):
        if len(calls) == 1:
            yield {0: DeltaToolCall(name="work", json_args="{}", tool_call_id="work-once")}
        elif len(calls) in (2, 3):
            raise ModelContextWindowExceededError("test", "too large")
        elif len(calls) == 4:
            assert "completed side effect" in str(messages)
            yield {0: DeltaToolCall(name="work", json_args="{}", tool_call_id="new-work")}
        else:
            yield "done"

    model = RecordingModel(stream_function=stream)
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model,
        capabilities=[CompactionCapability(), Capability(id="work", tools=[work])],
    )
    first = await executable.run("task")
    assert first.output_or_raise() == "done"
    assert executions == ["work", "work"]
    assert first.state.agent_context_state.get(CONTEXT_RECOVERY_STATE_ID).data["outcome"] == "resumed"
    second = (
        await HarnessBuilder().build(AgentSpec(), output_type=str, model=model).run("next", previous_state=first.state)
    )
    assert second.output_or_raise() == "done"
    assert len(calls) == 6
    for messages, settings, parameters in calls[3:]:
        assert settings["openai_previous_response_id"] is None
        assert settings["openai_conversation_id"] is None
        assert settings["google_cached_content"] is None
        assert settings["xai_previous_response_id"] is None
        from pydantic_ai.models.openai import OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider

        native = OpenAIResponsesModel(
            "gpt-4.1",
            provider=OpenAIProvider(api_key="test-only"),
            settings={"openai_conversation_id": "conv_old", "openai_previous_response_id": "resp_old"},
        )
        prepared_settings, _ = native.prepare_request(settings, parameters)
        assert prepared_settings["openai_conversation_id"] is None
        assert prepared_settings["openai_previous_response_id"] is None
        previous_id, conversation_id, sent = native._resolve_server_side_state(prepared_settings, messages)
        assert previous_id is None and conversation_id is None and sent == messages


@pytest.mark.anyio
@pytest.mark.parametrize("limit", [1, 2, 3])
async def test_context_recovery_shares_request_limit(limit: int) -> None:
    from pydantic_ai.usage import UsageLimits

    calls = 0

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise ModelContextWindowExceededError("test", "too large")
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream), capabilities=[CompactionCapability()]
    )
    result = await executable.run("task", usage_limits=UsageLimits(request_limit=limit))
    assert calls <= limit
    assert (result.status == "completed") is (limit == 3)


@pytest.mark.anyio
@pytest.mark.parametrize("storage", ["readable", "missing-reader", "write-failure", "too-large"])
async def test_retained_history_is_readable_after_run_and_failures_do_not_block(tmp_path, storage) -> None:
    from a13n_harness.environment import (
        DynamicEnvironmentCapability,
        DynamicEnvironmentConfiguration,
        EnvironmentAction,
        EnvironmentMount,
        EnvironmentPermissionSet,
    )
    from a13n_harness.environment.advanced import create_environment_runtime
    from a13n_harness.providers.environment.direct_local.configuration import (
        DirectLocalEnvironmentConfiguration,
        DirectLocalRootConfiguration,
    )
    from pydantic_ai.models.function import DeltaToolCall

    from .environment_helpers import DirectLocalSource

    source = DirectLocalSource(
        DirectLocalEnvironmentConfiguration(root=DirectLocalRootConfiguration(path=tmp_path)),
        environment_id="context-recovery-test",
    )
    environment = create_environment_runtime(
        mounts={
            "local": EnvironmentMount(
                source=source, permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction))
            )
        },
        default_mount="local",
    )
    if storage == "write-failure":
        (tmp_path / "history").write_text("not a directory")
    calls = []

    async def stream(messages, info):
        calls.append(deepcopy(messages))
        if len(calls) <= 2:
            raise ModelContextWindowExceededError("test", "too large")
        if len(calls) == 3 and storage == "readable":
            import json

            path = next((tmp_path / "history").iterdir())
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps(
                        {"file_path": "/workspace/history/" + path.name, "line_offset": 2, "line_limit": 20}
                    ),
                    tool_call_id="read-history",
                )
            }
        else:
            yield "done"

    capabilities = [CompactionCapability()]
    if storage != "missing-reader":
        capabilities.append(DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()))
    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream), capabilities=capabilities
    )
    text = "original evidence " + "x" * (11 * 1024 * 1024 if storage == "too-large" else 40000)
    result = await executable.run(
        text, bindings=RunBindings.embedded(environment=environment, retained_history_directory="/workspace/history")
    )
    assert result.output_or_raise() == "done"
    assert "Context recovery" in str(calls[2])
    if storage in {"readable", "too-large"}:
        retained = next((tmp_path / "history").iterdir())
        content = retained.read_text()
        assert "original evidence" in content
        assert max(map(len, content.splitlines())) <= 1002
        assert "Historical evidence" in content
        if storage == "readable":
            returns = [p for m in calls[3] for p in m.parts if isinstance(p, ToolReturnPart)]
            assert returns and "original evidence" in str(returns[-1].content)
        else:
            assert retained.stat().st_size > 10 * 1024 * 1024
    else:
        assert "Retained original history:" not in str(calls[2])
        assert "History preservation:" in str(calls[2])
        if storage != "write-failure":
            assert not (tmp_path / "history").exists()


def test_media_loss_and_unknown_provenance_are_explicit() -> None:
    from pydantic_ai.messages import BinaryContent, UserPromptPart

    rebuilt = reconstruct(
        [ModelRequest(parts=[UserPromptPart(["legacy task", BinaryContent(b"binary", media_type="image/png")])])]
    )
    assert "provenance is unavailable" in rebuilt.prompt
    assert "payload_omitted" in rebuilt.prompt
    assert "legacy task" in rebuilt.prompt
    assert "Latest task-start input" not in rebuilt.prompt


@pytest.mark.anyio
async def test_default_recovery_first_uses_successful_normal_compaction() -> None:
    from a13n_harness._context_recovery import CONTEXT_RECOVERY_STATE_ID

    calls = []

    async def stream(messages, info):
        calls.append(deepcopy(messages))
        if len(calls) == 1:
            raise ModelContextWindowExceededError("test", "too large")
        if len(calls) == 2:
            yield "Semantic summary of unfinished work"
        else:
            yield "done"

    result = (
        await HarnessBuilder()
        .build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream))
        .run("task")
    )
    assert result.output_or_raise() == "done"
    assert len(calls) == 3
    assert "Semantic summary of unfinished work" in str(calls[-1])
    assert result.state.agent_context_state.get(CONTEXT_RECOVERY_STATE_ID) is None


@pytest.mark.anyio
@pytest.mark.parametrize("enabled", [False, True])
async def test_recovery_policy_controls_default_runtime(enabled, monkeypatch) -> None:
    import a13n_harness._context_recovery as recovery

    async def unexpected_preservation(*args, **kwargs):
        raise AssertionError("preservation is disabled")

    monkeypatch.setattr(recovery, "preserve_history", unexpected_preservation)
    calls = []

    async def stream(messages, info):
        calls.append(deepcopy(messages))
        if len(calls) <= 2:
            raise ModelContextWindowExceededError("test", "too large")
        yield "done"

    policy = ContextRecoveryPolicy(enabled=enabled, max_history_chars=3000, preserve_history=False)
    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream), context_recovery=policy
    )
    result = await executable.run("task" * 5000)
    if enabled:
        assert result.output_or_raise() == "done"
        state = result.state.agent_context_state.get(recovery.CONTEXT_RECOVERY_STATE_ID)
        assert state is not None and isinstance(state.data, dict)
        assert state.data["after_chars"] <= 3000
        assert state.data["preservation"] == "disabled by context recovery policy"
    else:
        assert result.status == "failed"
        assert len(calls) == 1


def test_recovery_policy_rejects_unusable_budget() -> None:
    with pytest.raises(ValueError, match="max_history_chars"):
        ContextRecoveryPolicy(max_history_chars=2047)


@pytest.mark.anyio
async def test_cancellation_during_preservation_propagates(monkeypatch) -> None:
    import asyncio

    import a13n_harness._context_recovery as recovery

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(recovery, "preserve_history", cancelled)

    async def stream(messages, info):
        raise ModelContextWindowExceededError("test", "too large")
        yield "unreachable"

    with pytest.raises(asyncio.CancelledError):
        await (
            HarnessBuilder()
            .build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream))
            .run("task")
        )


@pytest.mark.anyio
@pytest.mark.parametrize("proactive", [False, True])
@pytest.mark.parametrize("summary_overflows", [False, True])
async def test_primary_progress_allows_compaction_for_a_later_overflow(proactive, summary_overflows) -> None:
    from a13n_harness._context_recovery import CONTEXT_RECOVERY_STATE_ID
    from pydantic_ai.capabilities import Capability
    from pydantic_ai.models.function import DeltaToolCall

    calls = 0
    executions = 0

    async def work() -> str:
        nonlocal executions
        executions += 1
        return "completed work"

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        if calls in (1, 4) or (calls == 5 and summary_overflows):
            raise ModelContextWindowExceededError("test", "too large")
        if calls in (2, 5):
            yield "Semantic summary of unfinished work"
        elif calls == 3:
            yield {0: DeltaToolCall(name="work", json_args="{}", tool_call_id="work-once")}
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=[Capability(id="work", tools=[work]), *([CompactionCapability()] if proactive else [])],
        context_recovery=ContextRecoveryPolicy(preserve_history=False),
    )
    result = await executable.run("task")
    assert result.output_or_raise() == "done"
    assert calls == 6
    assert executions == 1
    marker = result.state.agent_context_state.get(CONTEXT_RECOVERY_STATE_ID)
    assert (marker is not None) is summary_overflows
    if marker is not None:
        assert marker.data["outcome"] == "resumed"


@pytest.mark.anyio
async def test_successful_summary_does_not_reset_compaction_without_primary_progress() -> None:
    from a13n_harness._context_recovery import CONTEXT_RECOVERY_STATE_ID

    calls = 0

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        if calls == 2:
            yield "Semantic summary"
        else:
            raise ModelContextWindowExceededError("test", "too large")

    result = (
        await HarnessBuilder()
        .build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream))
        .run("task")
    )
    assert result.status == "failed"
    assert calls == 3
    assert result.state.agent_context_state.get(CONTEXT_RECOVERY_STATE_ID) is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "ending", ["value-error", "http-error", "usage-limit", "retry-exhausted", "resumed", "overflow"]
)
async def test_reconstruction_records_terminal_outcome_after_transient_retries(ending) -> None:
    from a13n_harness import HarnessEvent, HarnessExtensionEvent, ModelRecoveryPolicy
    from a13n_harness._context_recovery import CONTEXT_RECOVERY_STATE_ID
    from pydantic_ai.exceptions import ModelHTTPError
    from pydantic_ai.usage import UsageLimits

    calls = 0

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        if calls <= 2 or ending == "overflow":
            raise ModelContextWindowExceededError("test", "too large")
        if ending == "value-error":
            raise ValueError("terminal failure")
        if ending == "http-error":
            raise ModelHTTPError(400, "test", "terminal failure")
        if ending in {"retry-exhausted", "usage-limit"} or (ending == "resumed" and calls == 3):
            raise ModelHTTPError(503, "test", "temporary failure")
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        context_recovery=ContextRecoveryPolicy(preserve_history=False),
        model_recovery=ModelRecoveryPolicy(
            enabled=True, max_attempts=2, backoff_initial_seconds=0, backoff_max_seconds=0
        ),
    )
    bindings = RunBindings.embedded(context_recovery_scope_id="host-run")
    async with executable.stream(
        "task", bindings=bindings, usage_limits=UsageLimits(request_limit=3 if ending == "usage-limit" else 10)
    ) as run:
        events = [event async for event in run]
        result = run.result
    assert result is not None
    expected = "resumed" if ending == "resumed" else "overflow" if ending == "overflow" else "failed"
    assert result.status == ("completed" if ending == "resumed" else "failed")
    marker = result.state.agent_context_state.get(CONTEXT_RECOVERY_STATE_ID)
    assert marker is not None
    assert marker.data["pending"] is False
    assert marker.data["outcome"] == expected
    outcomes = [
        event.event.payload["outcome"]
        for event in events
        if isinstance(event, HarnessEvent)
        and isinstance(event.event, HarnessExtensionEvent)
        and event.event.kind == "recovery"
        and isinstance(event.event.payload, dict)
        and "outcome" in event.event.payload
    ]
    assert outcomes == ["prepared", expected]
    assert calls == (4 if ending in {"retry-exhausted", "resumed"} else 3)

    # A terminal failure must not refund the logical Host Run's reconstruction allowance.
    calls = 0
    second = await executable.run("continue", previous_state=result.state, bindings=bindings)
    assert second.failure is not None and second.failure.code == "context_recovery_exhausted"
    assert calls == 1
