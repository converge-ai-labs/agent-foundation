from __future__ import annotations

import json
from collections.abc import AsyncIterator
from copy import deepcopy
from hashlib import sha256

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, HarnessState, RunBindings
from a13n_harness.capabilities import CompactionCapability, CompactionPolicy, HandoffCapability
from a13n_harness.capabilities.context import (
    _COMPACTION_PROMPT,
    _build_compacted_history,
    _build_restored_history,
    _HandoffState,
)
from a13n_harness.model_context import (
    ModelContextBlock,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextProjectionRequest,
    ModelContextRequestKind,
    _commit_projection,
    user_prompt_content,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextContent,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.usage import RequestUsage

pytestmark = pytest.mark.anyio

_LEGACY_OWNERSHIP_KEY = "a13n.model-context-overlay"


def _user_text(messages: list[ModelMessage]) -> list[str]:
    return [
        content.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
        for content in user_prompt_content(part)
        if isinstance(content, TextContent)
    ]


@pytest.mark.parametrize("kind", ["handoff", "compaction"])
@pytest.mark.parametrize("ownership", [{"version": "1", "parts": [{"index": 999, "sha256": "stale"}]}, "invalid"])
def test_replacement_drops_only_legacy_metadata_without_inspecting_discarded_parts(
    kind: str, ownership: object
) -> None:
    historical = ModelRequest(
        parts=[SystemPromptPart("Standing instructions"), UserPromptPart("discarded overlay")],
        metadata={_LEGACY_OWNERSHIP_KEY: ownership, "caller": {"retain": True}},
    )
    messages = [historical]
    retained = (ModelRequest(parts=[UserPromptPart("discarded overlay")]),)
    before = ModelMessagesTypeAdapter.dump_json(messages)
    if kind == "handoff":
        rebuilt = _build_restored_history(
            messages,
            _HandoffState(operation_id="handoff-test", summary="Continuation summary"),
            retained_requests=retained,
        )
    else:
        rebuilt = _build_compacted_history(messages, "Continuation summary", retained_requests=retained)

    assert rebuilt[0].metadata is not None
    assert rebuilt[0].metadata["caller"] == {"retain": True}
    assert _LEGACY_OWNERSHIP_KEY not in rebuilt[0].metadata
    assert rebuilt[0].parts[0] == historical.parts[0]
    assert _user_text(rebuilt).count("discarded overlay") == 1
    assert rebuilt[-1].parts == retained[0].parts
    assert retained[0].metadata is None
    assert ModelMessagesTypeAdapter.dump_json(messages) == before


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("resume", [False, True])
async def test_compaction_then_handoff_preserves_raw_input_not_old_overlays(legacy: bool, resume: bool) -> None:
    shared_text = "User input identical to an old overlay"
    original = ModelRequest(parts=[SystemPromptPart("Stable instructions"), UserPromptPart("Old task")])
    history = _commit_projection(
        [original],
        ModelContextProjectionRequest(kind=ModelContextRequestKind.INPUT),
        ModelContextProjection(
            blocks=(
                ModelContextBlock("test.environment", ModelContextPlacement.INPUT_PREAMBLE, shared_text),
                ModelContextBlock("test.runtime", ModelContextPlacement.REQUEST_EPILOGUE, "Old runtime overlay"),
            )
        ),
    )
    if legacy:
        history[0].metadata = {
            _LEGACY_OWNERSHIP_KEY: {
                "version": "1",
                "parts": [
                    {"index": 1, "source_id": "test.environment", "sha256": sha256(shared_text.encode()).hexdigest()}
                ],
            },
        }
    previous = HarnessState.new(
        message_history=tuple(
            [
                *history,
                ModelResponse(
                    parts=[TextPart("Previous answer")], usage=RequestUsage(input_tokens=2100, output_tokens=100)
                ),
            ]
        )
    )
    previous_json = previous.model_dump_json()
    compact_calls: list[list[ModelMessage]] = []

    async def compact_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        compact_calls.append(deepcopy(messages))
        if len(compact_calls) == 1:
            assert _COMPACTION_PROMPT in _user_text(messages)
            assert messages[: len(history)] == history
            assert "Old runtime overlay" in _user_text(messages)
            yield "Compacted conversation"
        elif not resume and len(compact_calls) == 2:
            yield {
                0: DeltaToolCall(
                    name="summarize",
                    json_args=json.dumps({"content": "Continue from the summary"}),
                    tool_call_id="handoff-call",
                )
            }
        else:
            yield "done"

    compact = HarnessBuilder().build(
        AgentSpec(system_prompt="Stable instructions"),
        output_type=str,
        model=FunctionModel(stream_function=compact_stream),
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=2000)), HandoffCapability()),
    )
    first = await compact.run(shared_text, previous_state=previous, bindings=RunBindings.embedded())
    assert first.output_or_raise() == "done"
    assert len(compact_calls) == (2 if resume else 3)
    assert first.state is not None
    assert previous.model_dump_json() == previous_json
    if not resume:
        assert _user_text(compact_calls[-1]).count(shared_text) == 1
        assert "Old runtime overlay" not in _user_text(compact_calls[-1])
        assert all(_LEGACY_OWNERSHIP_KEY not in (message.metadata or {}) for message in first.state.message_history)
        return
    saved = first.state.model_dump_json()
    restored = HarnessState.model_validate_json(saved)
    handoff_calls: list[list[ModelMessage]] = []

    async def handoff_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        handoff_calls.append(deepcopy(messages))
        if len(handoff_calls) == 1:
            yield {
                0: DeltaToolCall(
                    name="summarize",
                    json_args=json.dumps({"content": "Continue from the saved summary"}),
                    tool_call_id="handoff-call",
                )
            }
        else:
            yield "done"

    handoff = HarnessBuilder().build(
        AgentSpec(system_prompt="Stable instructions"),
        output_type=str,
        model=FunctionModel(stream_function=handoff_stream),
        capabilities=(HandoffCapability(),),
    )
    second = await handoff.run(shared_text, previous_state=restored, bindings=RunBindings.embedded())
    assert second.output_or_raise() == "done"
    assert len(handoff_calls) == 2
    replacement_text = _user_text(handoff_calls[-1])
    assert replacement_text.count(shared_text) == 1
    assert "Old runtime overlay" not in replacement_text
    assert "Old task" not in replacement_text
    assert second.state is not None
    for message in second.state.message_history:
        if isinstance(message, ModelRequest):
            assert _LEGACY_OWNERSHIP_KEY not in (message.metadata or {})
    assert previous.model_dump_json() == previous_json
    assert restored.model_dump_json() == saved
