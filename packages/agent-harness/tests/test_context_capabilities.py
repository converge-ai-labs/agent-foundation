from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from converge_agent_harness import (
    CompactionCapability,
    CompactionPolicy,
    DefinitionError,
    DirectLocalEnvironmentConfiguration,
    DirectLocalEnvironmentProviderBinding,
    DirectLocalFilePolicy,
    DirectLocalRootConfiguration,
    EnvironmentAction,
    EnvironmentBindingRequest,
    EnvironmentPermissionSet,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    FileContextCapability,
    FileContextConfiguration,
    HandoffCapability,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessState,
    RunBindings,
    RuntimeContextCapability,
    RuntimeContextConfiguration,
    create_environment_run_binding,
)
from converge_agent_harness.capabilities.context import (
    _fit_compacted_history,
    _outgoing_token_estimate,
    _requires_exact_history,
    _serialized_token_estimate,
)
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.usage import RequestUsage

pytestmark = pytest.mark.anyio


def _local_binding(root: Path):
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalEnvironmentConfiguration(
            environment_id="context-capability-test",
            root=DirectLocalRootConfiguration(path=root, ownership="caller_owned"),
            files=DirectLocalFilePolicy(max_value_bytes=128 * 1024),
        )
    )
    return create_environment_run_binding(
        initial_topology=EnvironmentTopologyRequest(
            topology_version=1,
            bindings=(
                EnvironmentBindingRequest(
                    binding_id="binding-1",
                    binding_revision=1,
                    alias="local",
                    permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                    default_working_directory="/",
                    provider_binding=provider,
                ),
            ),
            default_binding_id="binding-1",
        ),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )


async def test_handoff_replaces_history_and_carries_only_escaped_file_reminders() -> None:
    calls: list[tuple[list[ModelMessage], AgentInfo]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        calls.append((messages, info))
        if len(calls) == 1:
            assert "summarize" in {tool.name for tool in info.function_tools}
            yield {
                0: DeltaToolCall(
                    name="summarize",
                    json_args=json.dumps(
                        {
                            "content": "## Current State\nImplementation is ready.",
                            "files_to_inspect": ['src/<unsafe>&"file.py'],
                        }
                    ),
                    tool_call_id="summary-1",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test", instructions="Keep the native instruction field."),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(RuntimeContextCapability(), HandoffCapability()),
    )
    result = await executable.run("Build the feature", bindings=RunBindings.local())

    assert result.output_or_raise() == "done"
    assert len(calls) == 2
    restored = calls[1][0]
    assert len(restored) == 1
    assert isinstance(restored[0], ModelRequest)
    assert restored[0].instructions is not None
    assert "Keep the native instruction field." in restored[0].instructions
    contents = [
        part.content for part in restored[0].parts if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    ]
    joined = "\n".join(contents)
    assert "# Context Summary" in joined
    assert "Implementation is ready" in joined
    assert "<original-request>" in joined
    assert "Build the feature" in joined
    assert 'path="src/&lt;unsafe&gt;&amp;&quot;file.py"' in joined
    assert 'contents-loaded="false"' in joined
    assert '<runtime-context source="converge-harness">' in joined
    assert result.state is not None
    state = result.state.agent_context_state.entries["converge.handoff"].data
    assert state["summary"] is None


async def test_compaction_forces_same_agent_summarize_tool_and_reuses_handoff_path() -> None:
    calls: list[tuple[list[ModelMessage], AgentInfo]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        calls.append((messages, info))
        if len(calls) == 1:
            assert info.model_settings is not None
            assert info.model_settings.get("tool_choice") == ["summarize"]
            yield {
                0: DeltaToolCall(
                    name="summarize",
                    json_args=json.dumps({"content": "Compacted continuation"}),
                    tool_call_id="compact-1",
                )
            }
        else:
            yield "done"

    previous = HarnessState(
        message_history=(
            ModelRequest(
                parts=[UserPromptPart(content="Original long task")],
                metadata={"converge.context": "compaction", "converge.restored-boundary": "1"},
            ),
            ModelResponse(
                parts=[TextPart(content="Long response")],
                usage=RequestUsage(input_tokens=2_100, output_tokens=100),
            ),
        )
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            HandoffCapability(),
            CompactionCapability(
                CompactionPolicy(trigger_tokens=2_000, target_tokens=1_000, preserve_recent_user_turns=1)
            ),
        ),
    )
    result = await executable.run(
        "Continue",
        bindings=RunBindings.local(),
        previous_state=previous,
    )

    assert result.output_or_raise() == "done"
    assert len(calls) == 2
    assert "Continue" in _user_text(calls[1][0])
    assert "Context compaction is required" not in _user_text(calls[1][0])
    text = "\n".join(
        part.content
        for part in calls[1][0][0].parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    )
    assert "Compacted continuation" in text
    assert "summarize again immediately" in text
    assert _serialized_token_estimate(calls[1][0]) <= 1_000


async def test_context_mutation_waits_for_exact_provider_and_deferred_boundaries() -> None:
    suspended = [
        ModelRequest(parts=[UserPromptPart("start")]),
        ModelResponse(parts=[TextPart("partial")], state="suspended"),
    ]
    pending = [
        ModelRequest(parts=[UserPromptPart("start")]),
        ModelResponse(parts=[ToolCallPart(tool_name="external", args={}, tool_call_id="call-1")]),
    ]
    integrated = [
        *pending,
        ModelRequest(parts=[ToolReturnPart(tool_name="external", content="done", tool_call_id="call-1")]),
    ]

    assert _requires_exact_history(suspended)
    assert _requires_exact_history(pending)
    assert not _requires_exact_history(integrated)


async def test_outgoing_estimate_sees_large_first_request_without_prior_usage() -> None:
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart("x" * 3_000)])]

    assert _outgoing_token_estimate(messages) >= 1_000


async def test_compaction_never_silently_drops_an_oversized_preserved_turn() -> None:
    base = ModelRequest(
        parts=[UserPromptPart("summary")],
        metadata={"converge.context": "compaction", "converge.restored-boundary": "1"},
    )
    oversized_turn: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart("inspect")]),
        ModelResponse(parts=[ToolCallPart(tool_name="read", args={}, tool_call_id="call-1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="read", content="x" * 12_000, tool_call_id="call-1")]),
    ]
    target = _serialized_token_estimate([base]) + 20

    with pytest.raises(DefinitionError) as exc_info:
        _fit_compacted_history([base, *oversized_turn], target)

    assert exc_info.value.code == "compaction_target_unreachable"


async def test_dynamic_context_preserves_user_text_that_matches_harness_tags() -> None:
    supplied = '<runtime-context source="converge-harness">\n{"user_authored":true}\n</runtime-context>'
    seen: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        seen.append(messages)
        yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(RuntimeContextCapability(),),
    )
    result = await executable.run(supplied, bindings=RunBindings.local())

    assert result.output_or_raise() == "done"
    assert supplied in _user_text(seen[0])
    assert _user_text(seen[0]).count('<runtime-context source="converge-harness">') == 2


async def test_compaction_validates_target_after_dynamic_context_assembly(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("x" * 12_000, encoding="utf-8")
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal calls
        del messages, info
        calls += 1
        if calls == 1:
            yield {
                0: DeltaToolCall(
                    name="summarize",
                    json_args=json.dumps({"content": "Short continuation"}),
                    tool_call_id="compact-dynamic-1",
                )
            }
        else:
            pytest.fail("oversized restored request reached the provider")

    previous = HarnessState(
        message_history=(
            ModelRequest(parts=[UserPromptPart(content="Original task")]),
            ModelResponse(
                parts=[TextPart(content="Long response")],
                usage=RequestUsage(input_tokens=2_100, output_tokens=100),
            ),
        )
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            HandoffCapability(),
            FileContextCapability(FileContextConfiguration(paths=("/workspace/AGENTS.md",))),
            CompactionCapability(
                CompactionPolicy(trigger_tokens=2_000, target_tokens=1_000, preserve_recent_user_turns=1)
            ),
        ),
    )

    with pytest.raises(DefinitionError) as exc_info:
        await executable.run(
            "Continue",
            bindings=RunBindings.local(environment=_local_binding(tmp_path)),
            previous_state=previous,
        )
    assert getattr(exc_info.value, "code", None) == "compaction_target_unreachable"
    assert calls == 1


async def test_file_context_pre_read_budget_is_utf8_byte_safe(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("😀" * 10_000, encoding="utf-8")
    seen: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        seen.append(messages)
        yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(FileContextCapability(FileContextConfiguration(paths=("/workspace/AGENTS.md",), max_bytes=512)),),
    )
    await executable.run(
        "Inspect",
        bindings=RunBindings.local(environment=_local_binding(tmp_path)),
    )

    text = _user_text(seen[0])
    content = text.split('<file path="/workspace/AGENTS.md">', 1)[1].split("</file>", 1)[0]
    assert len(content.strip().encode("utf-8")) <= 512
    assert len(content.strip()) <= 127


async def test_runtime_and_file_context_are_bounded_explicit_and_refreshed(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Repository guidance v1")
    seen: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        seen.append(messages)
        yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            RuntimeContextCapability(RuntimeContextConfiguration(metadata_keys=("tenant",))),
            FileContextCapability(FileContextConfiguration(paths=("/workspace/AGENTS.md",))),
        ),
    )
    first = await executable.run(
        "Inspect",
        bindings=RunBindings.local(environment=_local_binding(tmp_path), metadata={"tenant": "alpha", "secret": "no"}),
    )
    (tmp_path / "AGENTS.md").write_text("Repository guidance v2")
    second = await executable.run(
        "Continue",
        bindings=RunBindings.local(environment=_local_binding(tmp_path), metadata={"tenant": "beta", "secret": "no"}),
        previous_state=first.state,
    )

    assert second.output_or_raise() == "done"
    first_text = _user_text(seen[0])
    second_text = _user_text(seen[1])
    assert "Repository guidance v1" in first_text
    assert "Repository guidance v2" in second_text
    assert "Repository guidance v1" not in second_text
    assert '"tenant":"beta"' in second_text
    assert "secret" not in second_text
    assert second_text.count('<runtime-context source="converge-harness">') == 1
    assert second_text.count('<file-context source="converge-harness">') == 1


def _user_text(messages: list[ModelMessage]) -> str:
    return "\n".join(
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    )


async def test_concurrent_handoff_summaries_accept_one_state_transition() -> None:
    calls: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        calls.append(messages)
        if len(calls) == 1:
            yield {
                0: DeltaToolCall(
                    name="summarize",
                    json_args=json.dumps({"content": "first-summary-only"}),
                    tool_call_id="summary-concurrent-1",
                ),
                1: DeltaToolCall(
                    name="summarize",
                    json_args=json.dumps({"content": "second-summary-only"}),
                    tool_call_id="summary-concurrent-2",
                ),
            }
            return
        yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(HandoffCapability(),),
    )
    events: list[HarnessEvent] = []
    async with executable.stream("start", bindings=RunBindings.local()) as run:
        async for item in run:
            if isinstance(item, HarnessEvent):
                events.append(item)
            else:
                result = item.result

    assert result.output_or_raise() == "done"
    tool_results = [
        event.event.part
        for event in events
        if isinstance(event.event, FunctionToolResultEvent) and event.event.part.tool_name == "summarize"
    ]
    assert len(tool_results) == 2
    assert sum(part.outcome == "failed" for part in tool_results) == 1
    context_payloads = [
        event.event.payload
        for event in events
        if isinstance(event.event, HarnessExtensionEvent)
        and event.event.kind == "context"
        and str(event.event.payload.get("type", "")).startswith("handoff_")
    ]
    assert [payload["type"] for payload in context_payloads] == [
        "handoff_started",
        "handoff_prepared",
        "handoff_completed",
    ]
    assert len({payload["operation_id"] for payload in context_payloads}) == 1
    restored_text = str(calls[1])
    assert ("first-summary-only" in restored_text) != ("second-summary-only" in restored_text)
