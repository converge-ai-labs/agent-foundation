from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from a13n_harness import AgentSpec as HarnessAgentSpec
from a13n_harness import (
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessModelCharacteristics,
    HarnessState,
    ModelCapability,
    ModelRecoveryPolicy,
    RunBindings,
)
from a13n_harness.capabilities import (
    CompactionCapability,
    CompactionPolicy,
    FileContextCapability,
    FileContextConfiguration,
    HandoffCapability,
    HandoffConfiguration,
    RuntimeContextCapability,
    RuntimeContextConfiguration,
    WorkingStateCapability,
    WorkspaceOutlineCapability,
    WorkspaceOutlineConfiguration,
)
from a13n_harness.capabilities.context import (
    _COMPACTION_PROMPT,
    _previous_assistant_reference,
    _requires_exact_history,
)
from a13n_harness.environment import (
    EnvironmentAction,
    EnvironmentPermissionSet,
)
from a13n_harness.environment.advanced import (
    create_environment_runtime,
)
from a13n_harness.environment.providers import (
    EnvironmentRuntimeMount,
)
from a13n_harness.model_context import (
    ModelContextBlock,
    ModelContextInputOrigin,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextProjectionRequest,
    ModelContextRequestKind,
    ModelInputEvent,
    _commit_projection,
    user_prompt_content,
)
from a13n_harness.providers.environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_harness.state import AgentContextStateSnapshot, CapabilityState
from pydantic_ai import ModelRetry
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import (
    BinaryContent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    TextContent,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RequestUsage, UsageLimits

from .environment_helpers import DirectLocalEnvironmentProviderBinding

pytestmark = pytest.mark.anyio


def _local_binding(root: Path, *, default_working_directory: str = "/"):
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalEnvironmentConfiguration(
            root=DirectLocalRootConfiguration(path=root),
            max_value_bytes=128 * 1024,
        ),
        environment_id="context-capability-test",
    )
    return create_environment_runtime(
        mounts={
            "local": EnvironmentRuntimeMount(
                binding=provider,
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                working_directory=default_working_directory,
            )
        },
        default_mount="local",
    )


def test_agent_spec_model_config_derives_context_capability_thresholds() -> None:
    spec = HarnessAgentSpec(
        model_characteristics=HarnessModelCharacteristics(
            capabilities=frozenset(
                {
                    ModelCapability.IMAGE_UNDERSTANDING,
                    ModelCapability.VIDEO_UNDERSTANDING,
                    ModelCapability.AUDIO_UNDERSTANDING,
                }
            ),
            context_window_tokens=200_000,
            proactive_context_management_threshold=0.65,
            compact_threshold=0.90,
        ),
    )
    executable = HarnessBuilder().build(
        spec,
        output_type=str,
        model=FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("done")])),
        capabilities=(HandoffCapability(), CompactionCapability()),
    )
    leaves: list[Any] = []
    executable._agent.root_capability.apply(leaves.append)
    handoff = next(capability for capability in leaves if isinstance(capability, HandoffCapability))
    compaction = next(capability for capability in leaves if isinstance(capability, CompactionCapability))

    assert spec.model_characteristics is not None
    dumped_configuration = spec.model_dump(mode="json", by_alias=True)["model_characteristics"]
    assert dumped_configuration["context_window_tokens"] == 200_000
    assert set(dumped_configuration["capabilities"]) == {
        "image_understanding",
        "video_understanding",
        "audio_understanding",
    }
    schema = HarnessAgentSpec.model_json_schema_with_capabilities()
    assert "model_characteristics" in schema["properties"]
    assert set(schema["$defs"]["ModelCapability"]["enum"]) == {
        "image_understanding",
        "video_understanding",
        "audio_understanding",
    }
    assert handoff.configuration.include_summary_reminder
    assert handoff.configuration.summary_reminder_tokens == 130_000
    assert compaction.policy is None


def test_handoff_model_config_distinguishes_unknown_context_from_disabled_reminder() -> None:
    cases = (
        (HarnessModelCharacteristics(), True, 0),
        (
            HarnessModelCharacteristics(
                context_window_tokens=200_000,
                proactive_context_management_threshold=None,
            ),
            False,
            0,
        ),
    )
    for model_characteristics, expected_enabled, expected_tokens in cases:
        executable = HarnessBuilder().build(
            HarnessAgentSpec(model_characteristics=model_characteristics),
            output_type=str,
            model=FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("done")])),
            capabilities=(HandoffCapability(),),
        )
        leaves: list[Any] = []
        executable._agent.root_capability.apply(leaves.append)
        handoff = next(capability for capability in leaves if isinstance(capability, HandoffCapability))

        assert handoff.configuration.include_summary_reminder is expected_enabled
        assert handoff.configuration.summary_reminder_tokens == expected_tokens


def test_explicit_context_capability_thresholds_override_agent_model_config() -> None:
    spec = HarnessAgentSpec(
        model_characteristics=HarnessModelCharacteristics(context_window_tokens=200_000),
    )
    executable = HarnessBuilder().build(
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


@pytest.mark.parametrize("native_history", [False, True])
async def test_handoff_replaces_history_and_carries_only_escaped_file_reminders(native_history: bool) -> None:
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

    executable = HarnessBuilder().build(
        AgentSpec(instructions="Keep the native instruction field."),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(RuntimeContextCapability(), HandoffCapability()),
    )
    previous = (
        HarnessState.new(
            message_history=(
                ModelRequest(parts=[UserPromptPart("Search for background information")]),
                ModelResponse(
                    parts=[
                        NativeToolCallPart("web_search", {}, tool_call_id="search-1"),
                        NativeToolReturnPart("web_search", "Background information", tool_call_id="search-1"),
                    ]
                ),
            )
        )
        if native_history
        else None
    )
    result = await executable.run("Build the feature", bindings=RunBindings.embedded(), previous_state=previous)

    assert result.output_or_raise() == "done"
    assert len(calls) == 2
    assert '<runtime-context source="a13n-harness">' in _user_text(calls[0][0])
    restored = calls[1][0]
    assert len(restored) == 1  # Native Pydantic preparation merges adjacent request messages.
    assert isinstance(restored[0], ModelRequest)
    assert restored[0].instructions is not None
    assert "Keep the native instruction field." in restored[0].instructions
    contents = [
        item.content
        for message in restored
        for part in message.parts
        if isinstance(part, UserPromptPart)
        for item in user_prompt_content(part)
    ]
    joined = "\n".join(contents)
    assert "# Context Summary" in joined
    assert "Implementation is ready" in joined
    assert "<original-request>" not in joined
    assert "Build the feature" in joined
    assert 'path="src/&lt;unsafe&gt;&amp;&quot;file.py"' in joined
    assert 'contents-loaded="false"' in joined
    assert '<runtime-context source="a13n-harness">' in joined
    assert "projected separately on ordinary requests" in joined
    assert "separately projected current structured state" in joined
    assert result.state is not None
    state = result.state.agent_context_state.entries["a13n.handoff"].data
    assert state["summary"] is None


async def test_handoff_reprojects_current_notes_after_history_replacement() -> None:
    calls: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        calls.append(messages)
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns and len(calls) == 1:
            yield {
                0: DeltaToolCall(
                    name="note_write",
                    json_args=json.dumps({"key": "decision", "value": "Keep current state"}),
                    tool_call_id="note-before-handoff",
                )
            }
        elif len(calls) == 2:
            yield {
                0: DeltaToolCall(
                    name="summarize",
                    json_args=json.dumps({"content": "Continue after the explicit handoff."}),
                    tool_call_id="handoff-after-note",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(HandoffCapability(), WorkingStateCapability()),
    )
    result = await executable.run("Track and continue", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    assert len(calls) == 3
    restored_text = _user_text(calls[-1])
    assert '<note key="decision">Keep current state</note>' in restored_text
    assert "projected separately on ordinary requests" in restored_text
    assert "note-before-handoff" not in str(calls[-1])


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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(HandoffCapability(),),
    )
    result = await executable.run(
        ("Describe the image", image),
        bindings=RunBindings.embedded(),
    )

    assert result.output_or_raise() == "done"
    restored_contents = [
        part.content
        for message in calls[1]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
        and not isinstance(part.content, str)
        and any(isinstance(item, BinaryContent) for item in part.content)
    ]
    assert len(restored_contents) == 1
    restored = restored_contents[0]
    assert "Describe the image" in restored
    restored_image = next(item for item in restored if isinstance(item, BinaryContent))
    assert restored_image.data == image.data
    assert restored_image.media_type == image.media_type
    assert result.state is not None
    assert "_wBpbWFnZQ==" in result.state.model_dump_json()


async def test_handoff_replays_delivered_multimodal_steering_in_order() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[list[ModelMessage]] = []
    image = BinaryContent(data=b"steering-image", media_type="image/png")

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        calls.append(messages)
        if len(calls) == 1:
            started.set()
            await release.wait()
            yield "Acknowledged"
        elif len(calls) == 2:
            yield {
                0: DeltaToolCall(
                    name="summarize",
                    json_args=json.dumps({"content": "Continue implementation."}),
                    tool_call_id="handoff-steering",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        HarnessAgentSpec(system_prompt="Keep the real system prompt."),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(HandoffCapability(),),
    )
    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart("Previous run input")]),
            ModelResponse(parts=[TextPart("Previous answer")]),
        )
    )
    events: list[HarnessEvent] = []
    async with executable.stream(
        "Initial current task", bindings=RunBindings.embedded(), previous_state=previous
    ) as run:
        consumer = asyncio.create_task(_consume_run(run, events))
        await started.wait()
        await run.steer(("Do not deploy; follow this image", image))
        pending_state = await run.export_state()
        assert len(pending_state.agent_context_state.entries["a13n.steering"].data["retained_requests"]) == 1
        release.set()
        result = await asyncio.wait_for(consumer, timeout=2)

    assert result.output_or_raise() == "done"
    assert len(calls) == 3
    restored_text = _user_text(calls[-1])
    assert "Previous run input" not in restored_text
    assert restored_text.count("Initial current task") == 1
    assert restored_text.count("Do not deploy; follow this image") == 1
    assert restored_text.index("Continue implementation.") < restored_text.index("Initial current task")
    assert restored_text.index("Initial current task") < restored_text.index("Do not deploy;")
    assert "Keep the real system prompt." in str(calls[-1])
    assert "Placeholder system prompt" not in str(calls[-1])
    restored_images = [
        item
        for message in calls[-1]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and not isinstance(part.content, str)
        for item in part.content
        if isinstance(item, BinaryContent)
    ]
    assert restored_images == [image]
    assert result.state is not None
    assert "Do not deploy; follow this image" in _user_text(list(result.state.message_history))
    # Restoring history is not another input delivery, even when it retains steering.
    fresh = [
        content.content
        for item in events
        if isinstance(item.event, ModelInputEvent)
        for content in item.event.content
        if isinstance(content, TextContent) and (content.metadata or {}).get("display") is not False
    ]
    assert fresh == ["Initial current task"]
    from pydantic_ai.messages import EnqueuedMessagesEvent

    assert sum(isinstance(item.event, EnqueuedMessagesEvent) for item in events) == 1
    restored = [
        content
        for message in result.state.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
        for content in user_prompt_content(part)
        if isinstance(content, TextContent)
    ]
    summary = next(content for content in restored if "# Context Summary" in content.content)
    assert summary.metadata["a13n.context"] == "handoff"
    protocol = [content for content in restored if (content.metadata or {}).get("source_id") == "a13n.context.protocol"]
    assert protocol and all(content.metadata["display"] is False for content in protocol)


@pytest.mark.parametrize("tool_choice", [None, "auto", "none"])
@pytest.mark.parametrize("codeact_enabled", [False, True])
async def test_compaction_uses_same_agent_plain_text_run_without_handoff(
    tool_choice: str | None, codeact_enabled: bool
) -> None:
    from a13n_harness.capabilities import CodeActCapability

    calls: list[tuple[list[ModelMessage], AgentInfo]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        calls.append((messages, info))
        if len(calls) == 1:
            assert (info.model_settings or {}).get("tool_choice") == tool_choice
            assert messages[: len(previous.message_history)] == list(previous.message_history)
            assert "Do not call any tools" in _user_text(messages)
            assert "Original long task" in _user_text(messages)
            assert "Continue" in _user_text(messages)
            assert "## Condensed conversation summary" in _user_text(messages)
            assert "9. Activated Skills:" in _user_text(messages)
            assert "11. Relevant Note Keys" in _user_text(messages)
            assert "Do not call any tools or investigate unresolved questions yourself." in _user_text(messages)
            assert "Record any uncertainties in the summary for the resumed agent to investigate." in _user_text(
                messages
            )
            assert _user_text(messages).count(historical_overlay) == 2
            yield "Compacted continuation"
        else:
            yield "done"

    historical_overlay = '<runtime-context source="a13n-harness">\n{"stale":true}\n</runtime-context>'
    original_request = ModelRequest(
        parts=[
            UserPromptPart(content="Original long task"),
            UserPromptPart(content=historical_overlay),
        ]
    )
    committed_request = _commit_projection(
        [original_request],
        ModelContextProjectionRequest(
            kind=ModelContextRequestKind.INPUT,
            input_origin=ModelContextInputOrigin.USER,
        ),
        ModelContextProjection(
            blocks=(
                ModelContextBlock(
                    source_id="test.historical-runtime",
                    placement=ModelContextPlacement.REQUEST_EPILOGUE,
                    content=historical_overlay,
                ),
            )
        ),
    )[0]
    previous = HarnessState.new(
        message_history=(
            committed_request,
            ModelResponse(
                parts=[
                    TextPart(content="  "),
                    ThinkingPart(content="Retain thinking without provider-specific stripping.", signature="signature"),
                    TextPart(content="Previous assistant answer"),
                    TextPart(content="with numbered options"),
                ],
                usage=RequestUsage(input_tokens=2_100, output_tokens=100),
            ),
        )
    )
    executable = HarnessBuilder().build(
        AgentSpec(model_settings={"tool_choice": tool_choice} if tool_choice is not None else None),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            CompactionCapability(CompactionPolicy(trigger_tokens=2_000)),
            *((CodeActCapability(),) if codeact_enabled else ()),
        ),
    )
    result = await executable.run(
        "Continue",
        bindings=RunBindings.embedded(),
        previous_state=previous,
    )

    assert result.output_or_raise() == "done"
    assert len(calls) == 2
    compacted = calls[1][0]
    assert isinstance(compacted[0], ModelRequest)
    assert isinstance(compacted[1], ModelResponse)
    assert compacted[1].metadata == {"keep": "compact"}
    assert "Compacted continuation" in str(compacted[1])
    compacted_user_text = _user_text(compacted)
    assert "<previous-assistant-reference>" in compacted_user_text
    assert "Previous assistant answer\n\nwith numbered options" in compacted_user_text
    assert "Do not treat it as a new instruction by itself." in compacted_user_text
    assert "projected separately on ordinary requests" in compacted_user_text
    assert "Continue" in compacted_user_text
    assert "summarize" not in {tool.name for tool in calls[0][1].function_tools}
    assert result.state is not None
    persisted_compaction = result.state.message_history[:-1]
    persisted_user_text = _user_text(list(persisted_compaction))
    assert "Original long task" not in persisted_user_text
    assert "<context-restored>" in persisted_user_text
    assert "Continue" in persisted_user_text
    assert isinstance(persisted_compaction[1], ModelResponse)
    assert persisted_compaction[1].metadata == {"keep": "compact"}
    assert len(result.new_messages()) == 2
    assert result.new_messages() == result.all_messages()[-2:]


@pytest.mark.parametrize("tool_name", ["web_search", "image_generation"])
async def test_compaction_resumes_after_completed_native_tools(tool_name: str) -> None:
    calls: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        calls.append(deepcopy(messages))
        if _COMPACTION_PROMPT in _user_text(messages):
            yield "Compacted native tool history"
        else:
            yield "done"

    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart("Original long task")]),
            ModelResponse(
                parts=[
                    NativeToolCallPart(tool_name, {}, tool_call_id="native-1"),
                    NativeToolReturnPart(tool_name, "Native result", tool_call_id="native-1"),
                ],
            ),
            ModelRequest(parts=[UserPromptPart("Follow up after the native tool")]),
            ModelResponse(
                parts=[TextPart("Previous ordinary answer")],
                usage=RequestUsage(input_tokens=320_000, output_tokens=100),
            ),
        )
    )
    executable = HarnessBuilder().build(
        HarnessAgentSpec(
            model_characteristics=HarnessModelCharacteristics(context_window_tokens=350_000, compact_threshold=0.9)
        ),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(CompactionCapability(), RuntimeContextCapability()),
    )
    result = await executable.run("Continue", bindings=RunBindings.embedded(), previous_state=previous)

    assert result.output_or_raise() == "done"
    assert len(calls) == 2
    assert calls[0][: len(previous.message_history)] == list(previous.message_history)
    assert _COMPACTION_PROMPT in _user_text(calls[0])
    assert _COMPACTION_PROMPT not in _user_text(calls[1])
    assert "Original long task" not in _user_text(calls[1])
    assert '<runtime-context source="a13n-harness">' in _user_text(calls[1])
    assert result.state is not None
    assert any(
        isinstance(message, ModelResponse)
        and message.metadata == {"keep": "compact"}
        and any(
            isinstance(part, TextPart) and part.content == "Compacted native tool history" for part in message.parts
        )
        for message in result.state.message_history
    )
    assert not any(
        isinstance(part, NativeToolCallPart | NativeToolReturnPart)
        for message in result.state.message_history
        for part in message.parts
    )


async def test_compaction_preserves_native_provider_cache_prefixes() -> None:
    calls = []

    class RecordingModel(FunctionModel):
        @asynccontextmanager
        async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
            calls.append((deepcopy(messages), deepcopy(model_settings), deepcopy(model_request_parameters)))
            async with super().request_stream(
                messages, model_settings, model_request_parameters, run_context
            ) as response:
                yield response

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if _COMPACTION_PROMPT in _user_text(messages):
            yield "Summary: continue the requested work."
        elif len(calls) == 1:
            yield {
                0: DeltaToolCall(
                    name="note_write",
                    json_args=json.dumps({"key": "decision", "value": "Keep the decision"}),
                    tool_call_id="cache-note",
                )
            }
        else:
            yield "Ordinary answer."

    model = RecordingModel(stream_function=stream)

    def build(threshold: int):
        return HarnessBuilder().build(
            HarnessAgentSpec(system_prompt="Stable system prompt.", instructions="Stable instructions."),
            output_type=str,
            model=model,
            capabilities=(
                RuntimeContextCapability(),
                WorkingStateCapability(),
                CompactionCapability(CompactionPolicy(trigger_tokens=threshold)),
            ),
        )

    first = await build(1000000).run("First input.", bindings=RunBindings.embedded())
    assert first.output_or_raise() == "Ordinary answer."
    second = await build(1).run("Continue.", previous_state=first.state, bindings=RunBindings.embedded())
    assert second.output_or_raise() == "Ordinary answer."
    assert len(calls) == 4
    assert _COMPACTION_PROMPT in _user_text(calls[2][0])

    chat = OpenAIChatModel("gpt-4.1", provider=OpenAIProvider(api_key="test-only"))
    responses = OpenAIResponsesModel("gpt-4.1", provider=OpenAIProvider(api_key="test-only"))
    claude = AnthropicModel("claude-sonnet-4-6", provider=AnthropicProvider(api_key="test-only"))
    projections = []
    for messages, settings, parameters in calls[:3]:
        chat_messages = await chat._map_messages(messages, parameters, model_settings=settings)
        responses_instructions, responses_messages = await responses._map_messages(messages, settings, parameters)
        claude_system, claude_messages = await claude._map_message(messages, parameters, settings)
        claude_tools = claude._prepare_tools_and_tool_choice(settings, parameters)
        projections.append((chat_messages, responses_messages, claude_messages))
        if len(projections) == 1:
            stable = (responses_instructions, claude_system, claude_tools, parameters.function_tools, settings)
        else:
            assert (responses_instructions, claude_system, claude_tools, parameters.function_tools, settings) == stable
    for previous, current in pairwise(projections):
        for prefix, extended in zip(previous, current, strict=True):
            assert extended[: len(prefix)] == prefix


def test_previous_assistant_reference_is_bounded_with_head_and_tail() -> None:
    visible = "h" * 24_000 + "removed" * 1_000 + "t" * 6_000
    reference = _previous_assistant_reference(
        [
            ModelResponse(parts=[TextPart(content="older")]),
            ModelResponse(
                parts=[TextPart(content=visible), ToolCallPart(tool_name="ignored", args={}, tool_call_id="1")]
            ),
            ModelRequest(parts=[UserPromptPart(content="select 1")]),
        ]
    )

    assert reference is not None
    assert reference.startswith("h" * 24_000)
    assert "[... 7000 chars truncated from previous assistant response ...]" in reference
    assert reference.endswith("t" * 6_000)
    assert "removed" not in reference


async def test_compaction_retains_only_applied_inputs_from_the_current_logical_run() -> None:
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
        elif _COMPACTION_PROMPT in _user_text(messages):
            yield "Retained compact summary"
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=1_000)),),
    )
    async with executable.stream("Initial task", bindings=RunBindings.embedded()) as run:
        consumer = asyncio.create_task(_consume_run(run))
        await started.wait()
        enqueue_id = await run.steer(("Steer toward the new requirement",))
        accepted_state = await run.export_state()
        accepted = accepted_state.agent_context_state.entries["a13n.steering"].data["retained_requests"]
        assert len(accepted) == 1
        release.set()
        first = await asyncio.wait_for(consumer, timeout=2)

    assert enqueue_id
    assert first.state is not None
    retained_state = first.state.agent_context_state
    retained = retained_state.entries["a13n.steering"].data["retained_requests"]
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
        environment_states=first.state.environment_states,
    )
    phase = "compact"
    calls.clear()
    events: list[HarnessEvent] = []
    async with executable.stream("Next request", bindings=RunBindings.embedded(), previous_state=previous) as run:
        result = await _consume_run(run, events)
    fresh = [
        content.content
        for item in events
        if isinstance(item.event, ModelInputEvent)
        for content in item.event.content
        if isinstance(content, TextContent) and (content.metadata or {}).get("display") is not False
    ]
    assert fresh == ["Next request"]

    assert result.output_or_raise() == "done"
    assert len(calls) == 2
    final_text = _user_text(calls[-1][0])
    assert "Retained compact summary" in str(calls[-1][0])
    assert "Initial task" not in final_text
    assert "Steer toward the new requirement" not in final_text
    assert "Next request" in final_text


async def test_unapplied_steering_is_redelivered_across_model_recovery() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        calls.append(messages)
        if len(calls) == 1:
            started.set()
            await release.wait()
            raise ConnectionResetError("stream disconnected before steering delivery")
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=ModelRecoveryPolicy(
            enabled=True,
            max_attempts=2,
            backoff_initial_seconds=0,
            backoff_max_seconds=0,
        ),
    )

    async with executable.stream("initial", bindings=RunBindings.embedded()) as run:
        consumer = asyncio.create_task(_consume_run(run))
        await started.wait()
        enqueue_id = await run.steer("recover this steering input")
        release.set()
        result = await asyncio.wait_for(consumer, timeout=2)

    assert enqueue_id
    assert result.output_or_raise() == "done"
    assert len(calls) == 2
    assert _user_text(calls[1]).count("recover this steering input") == 1


async def test_compaction_preserves_new_message_boundary_across_same_run_steering() -> None:
    first_ordinary_started = asyncio.Event()
    release_first_ordinary = asyncio.Event()
    calls: list[tuple[list[ModelMessage], AgentInfo]] = []
    compact_calls = 0
    ordinary_calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal compact_calls, ordinary_calls
        calls.append((messages, info))
        if _COMPACTION_PROMPT in _user_text(messages):
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
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=1)),),
    )

    async with executable.stream(
        "initial-current-input",
        bindings=RunBindings.embedded(),
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
        del info
        if _COMPACTION_PROMPT in _user_text(messages):
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
    executable = HarnessBuilder().build(
        AgentSpec(),
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

    result = await executable.run("Continue", bindings=RunBindings.embedded(), previous_state=previous)

    assert result.output_or_raise() == "done"
    assert validated == ["done"]
    assert len(executable._agent._output_validators) == 1


async def test_compaction_blocks_function_tool_dispatch() -> None:
    side_effects: list[str] = []

    async def danger() -> str:
        side_effects.append("executed")
        return "changed"

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if _COMPACTION_PROMPT in _user_text(messages):
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
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            Capability(tools=[danger], id="danger-tools"),
            CompactionCapability(CompactionPolicy(trigger_tokens=2_000)),
        ),
    )

    result = await executable.run("Continue", bindings=RunBindings.embedded(), previous_state=previous)

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
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=2_000)),),
    )

    result = await executable.run(
        "Continue",
        bindings=RunBindings.embedded(),
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
        del info
        calls += 1
        if _COMPACTION_PROMPT in _user_text(messages):
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
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=2_000)),),
    )

    result = await executable.run("Continue", bindings=RunBindings.embedded(), previous_state=previous)

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
                "a13n.handoff": CapabilityState(
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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(HandoffCapability(),),
    )
    result = await executable.run("Continue", bindings=RunBindings.embedded(), previous_state=previous)

    assert result.output_or_raise() == "done"
    assert result.state is not None
    migrated = result.state.agent_context_state.entries["a13n.handoff"].data
    assert "kind" not in migrated
    assert "preserve_recent_user_turns" not in migrated
    assert "target_tokens" not in migrated
    if kind == "compaction":
        assert migrated == {"files": [], "operation_id": None, "summary": None}


async def _consume_run(run: Any, events: list[HarnessEvent] | None = None) -> Any:
    result = None
    async for item in run:
        if isinstance(item, HarnessEvent):
            if events is not None:
                events.append(item)
        else:
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


@pytest.mark.parametrize("native_completed", [False, True])
@pytest.mark.parametrize("local_pending", [False, True])
@pytest.mark.parametrize("suspended", [False, True])
def test_exact_history_tracks_native_results_without_clearing_other_pending_calls(
    native_completed: bool, local_pending: bool, suspended: bool
) -> None:
    response = ModelResponse(
        parts=[
            NativeToolCallPart("web_search", {}, tool_call_id="native-1"),
            *([NativeToolReturnPart("web_search", "result", tool_call_id="native-1")] if native_completed else []),
            *([ToolCallPart("external", {}, tool_call_id="local-1")] if local_pending else []),
        ],
        state="suspended" if suspended else "complete",
    )
    history: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart("start")]), response]

    assert _requires_exact_history(history) is (suspended or local_pending or not native_completed)
    if not suspended:
        history.extend(
            [ModelRequest(parts=[UserPromptPart("follow up")]), ModelResponse(parts=[TextPart("ordinary answer")])]
        )
        assert _requires_exact_history(history) is (local_pending or not native_completed)
        if local_pending:
            history.append(ModelRequest(parts=[ToolReturnPart("external", "done", tool_call_id="local-1")]))
            assert _requires_exact_history(history) is (not native_completed)


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
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=1)),),
    )

    result = await executable.run("Continue", bindings=RunBindings.embedded(), previous_state=previous)

    assert result.output_or_raise() == "done"
    assert len(calls) == 1
    assert calls[0].model_settings is None or calls[0].model_settings.get("tool_choice") != "none"


async def test_dynamic_context_preserves_user_text_that_matches_harness_tags() -> None:
    supplied = '<runtime-context source="a13n-harness">\n{"user_authored":true}\n</runtime-context>'
    seen: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        seen.append(messages)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(RuntimeContextCapability(),),
    )
    result = await executable.run(supplied, bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    assert supplied in _user_text(seen[0])
    assert _user_text(seen[0]).count('<runtime-context source="a13n-harness">') == 2


async def test_compaction_failure_is_fail_open() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del info
        calls += 1
        if _COMPACTION_PROMPT in _user_text(messages):
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
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=2_000)),),
    )

    result = await executable.run("Continue", bindings=RunBindings.embedded(), previous_state=previous)

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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(FileContextCapability(FileContextConfiguration(paths=("/workspace/AGENTS.md",), max_bytes=512)),),
    )
    await executable.run(
        "Inspect",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path)),
    )

    text = _user_text(seen[0])
    content = text.split('<file path="/workspace/AGENTS.md">', 1)[1].split("</file>", 1)[0]
    assert len(content.strip().encode("utf-8")) <= 512
    assert "File context truncated by configured limits" in content
    assert len(content.split("\n[File context", 1)[0].strip()) <= 127


async def test_notes_workspace_and_file_context_are_input_only_while_runtime_and_handoff_follow_tools(
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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            Capability(tools=[inspect_workspace], id="test-tools"),
            WorkspaceOutlineCapability(WorkspaceOutlineConfiguration(max_depth=3)),
            FileContextCapability(FileContextConfiguration(paths=("extra.md",))),
            RuntimeContextCapability(RuntimeContextConfiguration(context_window_tokens=200_000)),
            HandoffCapability(HandoffConfiguration(summary_reminder_tokens=0)),
            WorkingStateCapability(),
        ),
    )
    result = await executable.run(
        "Inspect",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path, default_working_directory="/project")),
        previous_state=HarnessState.new(
            agent_context_state=AgentContextStateSnapshot(
                entries={
                    "a13n.working-state": CapabilityState(version="1", data={"notes": {"decision": "Keep prefix"}})
                }
            )
        ),
    )

    assert result.output_or_raise() == "done"
    assert len(seen) == 2
    input_text = _user_text(seen[0])
    assert "Workspace file outline (content not loaded)" in input_text
    assert '"path":"/workspace/project/src/module.py"' in input_text
    assert "Default repository guidance" in input_text
    assert "Explicit file guidance" in input_text
    assert '<note key="decision">Keep prefix</note>' in input_text
    assert '<context-reminder source="a13n.handoff">' not in input_text

    assert seen[1][0] == seen[0][0]
    tool_results_text = _user_text([seen[1][-1]])
    assert "Workspace file outline (content not loaded)" not in tool_results_text
    assert "Default repository guidance" not in tool_results_text
    assert "Explicit file guidance" not in tool_results_text
    assert '<notes source="a13n-harness">' not in tool_results_text
    assert '"latest_request_tokens":' in tool_results_text
    assert '"context_window_tokens":200000' in tool_results_text
    assert '"elapsed_seconds":' in tool_results_text
    assert '<context-reminder source="a13n.handoff">' in tool_results_text


async def test_runtime_and_file_context_preserve_history_and_refresh_current_values(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Repository guidance v1")
    seen: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        seen.append(messages)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            RuntimeContextCapability(RuntimeContextConfiguration(metadata_keys=("tenant",))),
            FileContextCapability(FileContextConfiguration(paths=("/workspace/AGENTS.md",))),
        ),
    )
    first = await executable.run(
        "Inspect",
        bindings=RunBindings.embedded(
            environment=_local_binding(tmp_path), metadata={"tenant": "alpha", "secret": "no"}
        ),
    )
    (tmp_path / "AGENTS.md").write_text("Repository guidance v2")
    second = await executable.run(
        "Continue",
        bindings=RunBindings.embedded(
            environment=_local_binding(tmp_path), metadata={"tenant": "beta", "secret": "no"}
        ),
        previous_state=first.state,
    )

    assert second.output_or_raise() == "done"
    first_text = _user_text(seen[0])
    second_text = _user_text(seen[1])
    assert "Repository guidance v1" in first_text
    assert ModelMessagesTypeAdapter.dump_json([seen[1][0]]) == ModelMessagesTypeAdapter.dump_json([seen[0][0]])
    assert "Repository guidance v1" in second_text
    assert "Repository guidance v2" in second_text
    assert '"tenant":"alpha"' in second_text
    assert '"tenant":"beta"' in second_text
    assert "secret" not in second_text
    assert second_text.count('<runtime-context source="a13n-harness">') == 2
    assert second_text.count('<file-context source="a13n-harness">') == 2


def _user_text(messages: list[ModelMessage]) -> str:
    text: list[str] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, UserPromptPart):
                continue
            text.extend(item.content for item in user_prompt_content(part) if isinstance(item, TextContent))
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

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(HandoffCapability(),),
    )
    events: list[HarnessEvent] = []
    async with executable.stream("start", bindings=RunBindings.embedded()) as run:
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
    from a13n_harness.toolsets.events import HandoffSummaryEvent

    summaries = [item.event for item in events if isinstance(item.event, HandoffSummaryEvent)]
    assert len(summaries) == 1
    assert summaries[0].operation_id == context_payloads[1]["operation_id"]
    assert summaries[0].tool_call_id in {"summary-concurrent-1", "summary-concurrent-2"}
    restored_text = str(calls[1])
    assert summaries[0].summary in _user_text(calls[1])
    assert ("first-summary-only" in restored_text) != ("second-summary-only" in restored_text)


@pytest.mark.parametrize("kind", ["handoff", "compaction"])
@pytest.mark.parametrize("retain_input", [False, True])
def test_restored_summary_is_read_from_content_not_the_input_boundary(kind: str, retain_input: bool) -> None:
    from a13n_harness.capabilities.context import (
        _build_compacted_history,
        _build_restored_history,
        _HandoffState,
        restored_history_summary,
    )

    original = [ModelRequest(parts=[UserPromptPart("Original input")])]
    retained = (ModelRequest(parts=[UserPromptPart("Retained input")]),) if retain_input else ()
    summary = "Accepted history summary"
    restored = (
        _build_restored_history(
            original, _HandoffState(operation_id="handoff-1", summary=summary), retained_requests=retained
        )
        if kind == "handoff"
        else _build_compacted_history(original, summary, retained_requests=retained)
    )
    saved = HarnessState.new(message_history=restored)
    reloaded = HarnessState.model_validate_json(saved.model_dump_json())
    assert restored_history_summary(reloaded.message_history) == summary
    assert restored_history_summary(original) is None
