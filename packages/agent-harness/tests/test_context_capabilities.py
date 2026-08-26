from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from converge_agent_environment_provider import (
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
)
from converge_agent_harness import AgentSpec as HarnessAgentSpec
from converge_agent_harness import (
    CompactionCapability,
    CompactionPolicy,
    EnvironmentAction,
    EnvironmentBindingRequest,
    EnvironmentPermissionSet,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    FileContextCapability,
    FileContextConfiguration,
    HandoffCapability,
    HandoffConfiguration,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessState,
    ModelConfiguration,
    RunBindings,
    RuntimeContextCapability,
    RuntimeContextConfiguration,
    WorkspaceOutlineCapability,
    WorkspaceOutlineConfiguration,
    create_environment_run_binding,
)
from converge_agent_harness.capabilities.context import _requires_exact_history
from converge_agent_harness.environment.local.binding import DirectLocalEnvironmentProviderBinding
from converge_agent_harness.state import AgentContextStateSnapshot, CapabilityState
from pydantic_ai import ModelRetry
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import (
    BinaryContent,
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
from pydantic_ai.usage import RequestUsage, UsageLimits

pytestmark = pytest.mark.anyio


def _local_binding(root: Path, *, default_working_directory: str = "/"):
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="context-capability-test",
            root=DirectLocalRootConfiguration(path=root),
            max_value_bytes=128 * 1024,
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
                    default_working_directory=default_working_directory,
                    provider_binding=provider,
                ),
            ),
            default_binding_id="binding-1",
        ),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )


def test_agent_spec_model_config_derives_context_capability_thresholds() -> None:
    spec = HarnessAgentSpec(
        model="logical:test",
        model_config=ModelConfiguration(
            context_window=200_000,
            proactive_context_management_threshold=0.65,
            compact_threshold=0.90,
        ),
    )
    executable = HarnessBuilder().build_code(
        spec,
        output_type=str,
        model=FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("done")])),
        capabilities=(HandoffCapability(), CompactionCapability()),
    )
    leaves: list[Any] = []
    executable._agent.root_capability.apply(leaves.append)
    handoff = next(capability for capability in leaves if isinstance(capability, HandoffCapability))
    compaction = next(capability for capability in leaves if isinstance(capability, CompactionCapability))

    assert spec.model_configuration is not None
    assert spec.model_dump(mode="json", by_alias=True)["model_config"]["context_window"] == 200_000
    assert "model_config" in HarnessAgentSpec.model_json_schema_with_capabilities()["properties"]
    assert handoff.configuration.include_summary_reminder
    assert handoff.configuration.summary_reminder_tokens == 130_000
    assert compaction.policy == CompactionPolicy(trigger_tokens=180_000)


def test_explicit_context_capability_thresholds_override_agent_model_config() -> None:
    spec = HarnessAgentSpec(
        model="logical:test",
        model_config=ModelConfiguration(context_window=200_000),
    )
    executable = HarnessBuilder().build_code(
        spec,
        output_type=str,
        model=FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("done")])),
        capabilities=(
            HandoffCapability(HandoffConfiguration(summary_reminder_tokens=12_345)),
            CompactionCapability(CompactionPolicy(trigger_tokens=23_456)),
        ),
    )
    leaves: list[Any] = []
    executable._agent.root_capability.apply(leaves.append)
    handoff = next(capability for capability in leaves if isinstance(capability, HandoffCapability))
    compaction = next(capability for capability in leaves if isinstance(capability, CompactionCapability))

    assert handoff.configuration.summary_reminder_tokens == 12_345
    assert compaction.policy == CompactionPolicy(trigger_tokens=23_456)


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


async def test_handoff_preserves_structured_multimodal_original_request() -> None:
    calls: list[list[ModelMessage]] = []
    image = BinaryContent(data=b"\xff\x00image", media_type="image/png")

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        calls.append(messages)
        if len(calls) == 1:
            yield {
                0: DeltaToolCall(
                    name="summarize",
                    json_args=json.dumps({"content": "Continue with the supplied image"}),
                    tool_call_id="summary-multimodal-1",
                )
            }
        else:
            del info
            yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(HandoffCapability(),),
    )
    result = await executable.run(
        ("Describe the image", image),
        bindings=RunBindings.local(),
    )

    assert result.output_or_raise() == "done"
    restored_contents = [
        part.content
        for message in calls[1]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and not isinstance(part.content, str)
    ]
    assert len(restored_contents) == 1
    restored = restored_contents[0]
    assert "Describe the image" in restored
    restored_image = next(item for item in restored if isinstance(item, BinaryContent))
    assert restored_image.data == image.data
    assert restored_image.media_type == image.media_type
    assert result.state is not None
    assert "_wBpbWFnZQ==" in result.state.model_dump_json()


async def test_compaction_uses_same_agent_plain_text_run_without_handoff() -> None:
    calls: list[tuple[list[ModelMessage], AgentInfo]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        calls.append((messages, info))
        if len(calls) == 1:
            assert info.model_settings is not None
            assert info.model_settings.get("tool_choice") == "none"
            assert "Original long task" in _user_text(messages)
            assert "Continue" in _user_text(messages)
            yield "Compacted continuation"
        else:
            yield "done"

    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart(content="Original long task")]),
            ModelResponse(
                parts=[TextPart(content="Previous assistant answer")],
                usage=RequestUsage(input_tokens=2_100, output_tokens=100),
            ),
        )
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=2_000)),),
    )
    result = await executable.run(
        "Continue",
        bindings=RunBindings.local(),
        previous_state=previous,
    )

    assert result.output_or_raise() == "done"
    assert len(calls) == 2
    compacted = calls[1][0]
    assert isinstance(compacted[0], ModelRequest)
    assert isinstance(compacted[1], ModelResponse)
    assert compacted[1].metadata == {"keep": "compact"}
    assert "Compacted continuation" in str(compacted[1])
    assert "Previous assistant answer" in _user_text(compacted)
    assert "Continue" in _user_text(compacted)
    assert "summarize" not in {tool.name for tool in calls[0][1].function_tools}
    assert len(result.new_messages()) == 2
    assert result.new_messages() == result.all_messages()[-2:]


async def test_compaction_replays_retained_initial_input_and_public_steering() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    phase = "steer"
    calls: list[tuple[list[ModelMessage], AgentInfo]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        calls.append((messages, info))
        if phase == "steer" and len(calls) == 1:
            started.set()
            await release.wait()
            yield "first response"
        elif info.model_settings is not None and info.model_settings.get("tool_choice") == "none":
            yield "Retained compact summary"
        else:
            yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=1_000)),),
    )
    async with executable.stream("Initial task", bindings=RunBindings.local()) as run:
        consumer = asyncio.create_task(_consume_run(run))
        await started.wait()
        enqueue_id = await run.steer(("Steer toward the new requirement",))
        release.set()
        first = await asyncio.wait_for(consumer, timeout=2)

    assert enqueue_id
    assert first.state is not None
    retained_state = first.state.agent_context_state
    retained = retained_state.entries["converge.steering"].data["retained_requests"]
    assert len(retained) == 2

    previous = HarnessState(
        schema_version="1",
        thread_id=first.state.thread_id,
        message_history=(
            ModelRequest(parts=[UserPromptPart(content="History to summarize")]),
            ModelResponse(
                parts=[TextPart(content="Previous response")],
                usage=RequestUsage(input_tokens=1_100, output_tokens=100),
            ),
        ),
        agent_context_state=retained_state,
        environment_state=first.state.environment_state,
    )
    phase = "compact"
    calls.clear()
    result = await executable.run("Next request", bindings=RunBindings.local(), previous_state=previous)

    assert result.output_or_raise() == "done"
    assert len(calls) == 2
    final_text = _user_text(calls[-1][0])
    assert "Retained compact summary" in str(calls[-1][0])
    assert "Initial task" in final_text
    assert "Steer toward the new requirement" in final_text
    assert "Next request" in final_text


async def test_compaction_preserves_new_message_boundary_across_same_run_steering() -> None:
    first_ordinary_started = asyncio.Event()
    release_first_ordinary = asyncio.Event()
    calls: list[tuple[list[ModelMessage], AgentInfo]] = []
    compact_calls = 0
    ordinary_calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal compact_calls, ordinary_calls
        calls.append((messages, info))
        if info.model_settings is not None and info.model_settings.get("tool_choice") == "none":
            compact_calls += 1
            yield f"compact-summary-{compact_calls}"
            return
        ordinary_calls += 1
        if ordinary_calls == 1:
            first_ordinary_started.set()
            await release_first_ordinary.wait()
            yield "first ordinary response"
            return
        yield "done"

    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart(content="old request")]),
            ModelResponse(
                parts=[TextPart(content="old response")],
                usage=RequestUsage(input_tokens=10, output_tokens=1),
            ),
        )
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=1)),),
    )

    async with executable.stream(
        "initial-current-input",
        bindings=RunBindings.local(),
        previous_state=previous,
    ) as run:
        consumer = asyncio.create_task(_consume_run(run))
        await first_ordinary_started.wait()
        enqueue_id = await run.steer("current-steering-input")
        release_first_ordinary.set()
        result = await asyncio.wait_for(consumer, timeout=2)

    assert enqueue_id
    assert result.output_or_raise() == "done"
    assert len(calls) == 4
    assert compact_calls == 2
    assert ordinary_calls == 2
    new_messages = result.new_messages()
    assert len(new_messages) == 3
    assert new_messages == result.all_messages()[-3:]
    assert isinstance(new_messages[0], ModelRequest)
    assert isinstance(new_messages[1], ModelRequest)
    assert isinstance(new_messages[2], ModelResponse)
    new_user_text = _user_text(list(new_messages))
    assert "initial-current-input" in new_user_text
    assert "current-steering-input" in new_user_text
    native_run_ids = {message.run_id for message in new_messages}
    assert None not in native_run_ids
    assert len(native_run_ids) == 1


async def test_compaction_clears_output_validators_only_on_the_agent_copy() -> None:
    validated: list[str] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        if info.model_settings is not None and info.model_settings.get("tool_choice") == "none":
            yield "Summary that is not the business output"
        else:
            yield "done"

    previous = HarnessState.new(
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
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=2_000)),),
    )

    @executable._agent.output_validator
    def validate_business_output(output: str) -> str:
        validated.append(output)
        if output != "done":
            raise ModelRetry("not the business output")
        return output

    result = await executable.run("Continue", bindings=RunBindings.local(), previous_state=previous)

    assert result.output_or_raise() == "done"
    assert validated == ["done"]
    assert len(executable._agent._output_validators) == 1


async def test_compaction_blocks_function_tool_dispatch() -> None:
    side_effects: list[str] = []

    async def danger() -> str:
        side_effects.append("executed")
        return "changed"

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del messages
        if info.model_settings is not None and info.model_settings.get("tool_choice") == "none":
            yield {
                0: DeltaToolCall(
                    name="danger",
                    json_args="{}",
                    tool_call_id="danger-1",
                )
            }
        else:
            yield "done"

    previous = HarnessState.new(
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
            Capability(tools=[danger], id="danger-tools"),
            CompactionCapability(CompactionPolicy(trigger_tokens=2_000)),
        ),
    )

    result = await executable.run("Continue", bindings=RunBindings.local(), previous_state=previous)

    assert result.output_or_raise() == "done"
    assert side_effects == []
    assert "Original task" in _user_text(list(result.all_messages()))


async def test_compaction_preserves_outer_request_limit() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages, info
        calls += 1
        yield "unexpected"

    previous = HarnessState.new(
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
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=2_000)),),
    )

    result = await executable.run(
        "Continue",
        bindings=RunBindings.local(),
        previous_state=previous,
        usage_limits=UsageLimits(request_limit=0),
    )

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "usage_limit_exceeded"
    assert result.usage.requests == 0
    assert calls == 0


async def test_compaction_fails_open_on_blank_summary() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages
        calls += 1
        if info.model_settings is not None and info.model_settings.get("tool_choice") == "none":
            yield "   "
        else:
            yield "done"

    previous = HarnessState.new(
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
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=2_000)),),
    )

    result = await executable.run("Continue", bindings=RunBindings.local(), previous_state=previous)

    assert result.output_or_raise() == "done"
    assert calls == 2
    assert "Original task" in _user_text(list(result.all_messages()))
    assert not any(
        isinstance(message, ModelResponse) and message.metadata == {"keep": "compact"}
        for message in result.all_messages()
    )


@pytest.mark.parametrize("kind", ["handoff", "compaction"])
async def test_handoff_migrates_legacy_v1_state(kind: str) -> None:
    operation_id = f"{kind}-legacy"
    previous = HarnessState.new(
        agent_context_state=AgentContextStateSnapshot(
            entries={
                "converge.handoff": CapabilityState(
                    version="1",
                    data={
                        "operation_id": operation_id,
                        "summary": "Legacy summary",
                        "files": [],
                        "kind": kind,
                        "preserve_recent_user_turns": 1,
                        "target_tokens": 1_000,
                    },
                )
            }
        )
    )

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(HandoffCapability(),),
    )
    result = await executable.run("Continue", bindings=RunBindings.local(), previous_state=previous)

    assert result.output_or_raise() == "done"
    assert result.state is not None
    migrated = result.state.agent_context_state.entries["converge.handoff"].data
    assert "kind" not in migrated
    assert "preserve_recent_user_turns" not in migrated
    assert "target_tokens" not in migrated
    if kind == "compaction":
        assert migrated == {"files": [], "operation_id": None, "summary": None}


async def _consume_run(run: Any) -> Any:
    result = None
    async for item in run:
        if not isinstance(item, HarnessEvent):
            result = item.result
    assert result is not None
    return result


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


async def test_compaction_does_not_estimate_history_without_provider_usage() -> None:
    calls: list[AgentInfo] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        calls.append(info)
        yield "done"

    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart(content="x" * 12_000)]),
            ModelResponse(parts=[TextPart(content="response without usage")]),
        )
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=1)),),
    )

    result = await executable.run("Continue", bindings=RunBindings.local(), previous_state=previous)

    assert result.output_or_raise() == "done"
    assert len(calls) == 1
    assert calls[0].model_settings is None or calls[0].model_settings.get("tool_choice") != "none"


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


async def test_compaction_failure_is_fail_open() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages
        calls += 1
        if info.model_settings is not None and info.model_settings.get("tool_choice") == "none":
            raise RuntimeError("compact model failed")
        yield "done"

    previous = HarnessState.new(
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
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=2_000)),),
    )

    result = await executable.run("Continue", bindings=RunBindings.local(), previous_state=previous)

    assert result.output_or_raise() == "done"
    assert calls == 2
    assert "Original task" in str(result.all_messages())


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


async def test_workspace_and_file_context_are_input_only_while_runtime_and_handoff_follow_tools(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("Default repository guidance", encoding="utf-8")
    (workspace / "extra.md").write_text("Explicit file guidance", encoding="utf-8")
    (workspace / "src").mkdir()
    (workspace / "src" / "module.py").write_text("value = 1", encoding="utf-8")
    seen: list[list[ModelMessage]] = []

    async def inspect_workspace() -> str:
        return "inspected"

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        seen.append(messages)
        if len(seen) == 1:
            assert "inspect_workspace" in {tool.name for tool in info.function_tools}
            yield {
                0: DeltaToolCall(
                    name="inspect_workspace",
                    json_args="{}",
                    tool_call_id="inspect-1",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            Capability(tools=[inspect_workspace], id="test-tools"),
            WorkspaceOutlineCapability(WorkspaceOutlineConfiguration(max_depth=3)),
            FileContextCapability(FileContextConfiguration(paths=("extra.md",))),
            RuntimeContextCapability(RuntimeContextConfiguration(context_window_tokens=200_000)),
            HandoffCapability(HandoffConfiguration(summary_reminder_tokens=0)),
        ),
    )
    result = await executable.run(
        "Inspect",
        bindings=RunBindings.local(environment=_local_binding(tmp_path, default_working_directory="/project")),
    )

    assert result.output_or_raise() == "done"
    assert len(seen) == 2
    input_text = _user_text(seen[0])
    assert "Workspace file outline (content not loaded)" in input_text
    assert '"path":"/workspace/project/src/module.py"' in input_text
    assert "Default repository guidance" in input_text
    assert "Explicit file guidance" in input_text
    assert '<context-reminder source="converge.handoff">' not in input_text

    tool_results_text = _user_text(seen[1])
    assert "Workspace file outline (content not loaded)" not in tool_results_text
    assert "Default repository guidance" not in tool_results_text
    assert "Explicit file guidance" not in tool_results_text
    assert '"context_window_tokens":200000' in tool_results_text
    assert '"elapsed_seconds":' in tool_results_text
    assert '<context-reminder source="converge.handoff">' in tool_results_text


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
    text: list[str] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, UserPromptPart):
                continue
            if isinstance(part.content, str):
                text.append(part.content)
            else:
                text.extend(item for item in part.content if isinstance(item, str))
    return "\n".join(text)


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
