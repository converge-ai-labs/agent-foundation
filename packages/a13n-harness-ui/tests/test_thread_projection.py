from __future__ import annotations

from typing import Literal
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness_ui.composition import CompositionAcceptanceService
from a13n_harness_ui.root_run import RootRunCoordinator
from a13n_harness_ui.storage import LocalStore
from a13n_harness_ui.subagent_operator import HarnessUiSubagentOperator
from a13n_harness_ui.surfaces import ReviewView, TranscriptEntry, TranscriptPart
from a13n_harness_ui.terminal_projection import TerminalProjectionService
from a13n_harness_ui.thread_projection import ThreadProjectionService, _request_parts
from pydantic_ai.messages import RetryPromptPart, ToolReturnPart


@pytest.mark.parametrize("outcome", ["success", "failed", "denied", "interrupted"])
@pytest.mark.parametrize(
    "content", ["plain result", None, {"ok": True}, "x" * (128 * 1024)], ids=["text", "none", "object", "large-text"]
)
def test_saved_tool_result_preserves_native_outcome(
    outcome: Literal["success", "failed", "denied", "interrupted"], content: object
) -> None:
    (part,) = _request_parts(
        ToolReturnPart(tool_name="view", tool_call_id="call-one", content=content, outcome=outcome)
    )
    assert part.kind == "tool_result"
    assert part.tool_call_id == "call-one"
    assert part.outcome == outcome
    assert TranscriptPart.model_validate_json(part.model_dump_json()).outcome == outcome


def test_retry_remains_distinct_and_older_projection_accepts_missing_outcome() -> None:
    (part,) = _request_parts(RetryPromptPart(content="Invalid input", tool_name="edit", tool_call_id="call-one"))
    assert part.kind == "retry"
    assert part.text == "Invalid input"
    assert part.outcome is None
    assert TranscriptPart.model_validate({"kind": "tool_result", "value": "old"}).outcome is None


@pytest.mark.parametrize("length", [4096, 4097, 256 * 1024 + 1])
def test_review_text_has_no_secondary_presentation_length_cap(length: int) -> None:
    text = "审" * length
    review = ReviewView(lifecycle="closed", kind="child", title="Reviewer", summary=text, content=text)
    restored = ReviewView.model_validate_json(review.model_dump_json())
    assert restored.summary == restored.content == text
    assert restored.truncated is False


@pytest.mark.anyio
@pytest.mark.parametrize("length", [4097, 128 * 1024])
async def test_retained_review_accepts_transcript_text_and_preserves_truncation(length: int) -> None:
    (part,) = _request_parts(RetryPromptPart(tool_name="view", tool_call_id="call-one", content="审" * length))
    threads = Mock(spec=ThreadProjectionService)
    threads.transcript_entry = AsyncMock(
        return_value=TranscriptEntry(position=0, message_kind="request", parts=(part,))
    )
    projections = TerminalProjectionService(
        store=Mock(spec=LocalStore),
        configurations=Mock(spec=CompositionAcceptanceService),
        threads=threads,
        root_runs=Mock(spec=RootRunCoordinator),
        children=Mock(spec=HarnessUiSubagentOperator),
        configuration_path=None,
    )
    review = await projections.retained_review(
        thread_id="thread-one", expected_continuation_id="a" * 64, position=0, tool_call_id="call-one"
    )
    assert review.summary == part.text
    assert review.value == part.value
    assert review.truncated is part.text_truncated
    assert review.omitted is part.value_omitted


def test_owned_context_summaries_are_not_authored_user_messages() -> None:
    from a13n_harness_ui.thread_projection import _message_entry
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    # Existing saved handoffs are recognized by producer metadata, never headings.
    entry = _message_entry(
        0,
        ModelRequest(
            parts=[UserPromptPart("# Context Summary\n\n**Saved context**"), UserPromptPart("Internal restoration")],
            metadata={"a13n.context": "handoff"},
        ),
    )
    assert entry.parts[0].metadata.model_extra["a13n.context"] == "handoff"
    assert not entry.parts[1].metadata.display
    compact = _message_entry(1, ModelResponse(parts=[TextPart("**Compacted**")], metadata={"keep": "compact"}))
    assert compact.parts[0].metadata.model_extra["a13n.context"] == "compaction"
    authored = _message_entry(2, ModelRequest(parts=[UserPromptPart("# Context Summary\n\nMy own words")]))
    assert authored.parts[0].metadata.display
    assert not authored.parts[0].metadata.model_extra


def test_tool_attachment_visibility_survives_history_roundtrip_without_hiding_user_media() -> None:
    from a13n_harness.content import ContentItem, ContentMetadata, annotate_prompt
    from a13n_harness_ui.thread_projection import _message_entry
    from pydantic_ai.messages import BinaryContent, ModelMessagesTypeAdapter, ModelRequest, UserPromptPart

    image = BinaryContent(data=b"\x89PNG", media_type="image/png")
    messages = ModelMessagesTypeAdapter.validate_json(
        ModelMessagesTypeAdapter.dump_json(
            [
                annotate_prompt(
                    ModelRequest(
                        parts=[
                            ToolReturnPart(tool_name="view", tool_call_id="call-image", content="Image attached."),
                            UserPromptPart([image]),
                            # Even in a mixed request, genuine user/steering media stays visible.
                            UserPromptPart([image]),
                        ]
                    ),
                    1,
                    [ContentItem(image, ContentMetadata(display=False))],
                )
            ]
        )
    )
    entry = _message_entry(0, messages[0])
    tool, attachment, authored = entry.parts
    assert tool.kind == "tool_result"
    assert tool.value == "Image attached."
    assert attachment.kind == "media"
    assert not attachment.metadata.display
    assert authored.kind == "media"
    assert authored.metadata.display


@pytest.mark.parametrize("kind", ["initial", "continuation", "display", "cleared"])
def test_inspection_reuses_native_history_and_preserves_display_context_split(kind, monkeypatch) -> None:
    from datetime import UTC, datetime

    import a13n_harness.state as state_module
    from a13n_harness import HarnessState
    from a13n_harness_ui.display_history import DisplayHistory, DisplayHistoryCollector, with_display_history
    from a13n_harness_ui.storage import ObjectKind, ObjectRef, StoredContinuation, StoredThreadInitialState
    from a13n_harness_ui.storage.contracts import AgentResourceSource, Thread, ThreadConfiguration
    from a13n_harness_ui.thread_projection import ThreadInspection, build_thread_inspection
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
    from pydantic_ai.usage import RequestUsage

    now = datetime.now(UTC)
    messages = (
        ModelRequest(parts=[UserPromptPart("Saved input")]),
        ModelResponse(parts=[TextPart("Saved answer")], usage=RequestUsage(input_tokens=7, output_tokens=3)),
    )
    state = HarnessState.new(thread_id="thread_one", message_history=messages if kind != "cleared" else ())
    if kind in {"display", "cleared"}:
        display = DisplayHistoryCollector(messages).capture(messages, completed=True)
        if kind == "cleared":
            display = DisplayHistoryCollector((), DisplayHistory(messages=display.messages)).capture(())
        state = with_display_history(state, display)
    composition = ObjectRef(object_kind=ObjectKind.run_composition, object_schema_version="1", logical_digest="a" * 64)
    initial = ObjectRef(object_kind=ObjectKind.thread_initial_state, object_schema_version="1", logical_digest="b" * 64)
    continuation = ObjectRef(object_kind=ObjectKind.continuation, object_schema_version="1", logical_digest="c" * 64)
    thread = Thread(
        thread_id="thread_one",
        created_at=now,
        updated_at=now,
        metadata_version=1,
        configuration=ThreadConfiguration(
            version=1, agent_source=AgentResourceSource(id="agent-one"), environment_profile_id="environment-native"
        ),
        initial_state=initial,
        continuation=None if kind == "initial" else continuation,
    )
    stored = (
        StoredThreadInitialState(harness_state=state, created_at=now)
        if kind == "initial"
        else StoredContinuation(
            harness_release="test", run_composition=composition, harness_state=state, created_at=now
        )
    )
    decode = Mock(wraps=state_module.decode_messages)
    monkeypatch.setattr(state_module, "decode_messages", decode)
    inspection = build_thread_inspection(thread, stored)
    # Saved display validation also checks the native mapping once. Projection
    # itself reuses one decoded native history for context and token metadata.
    assert decode.call_count == (2 if kind in {"display", "cleared"} else 1)
    metadata = ThreadInspection.model_validate_json(inspection.metadata_json)
    assert metadata.context_empty is (kind == "cleared")
    assert metadata.latest_request_tokens == (None if kind == "cleared" else 10)
    entries = [TranscriptEntry.model_validate_json(value) for value in inspection.entries]
    assert [part.text for entry in entries for part in entry.parts] == ["Saved input", "Saved answer"]


def test_saved_tool_media_retains_order_and_never_reveals_supplements() -> None:
    from pydantic_ai.messages import ImageUrl

    (part,) = _request_parts(
        ToolReturnPart(
            tool_name="view",
            tool_call_id="media-call",
            content=[
                ["before", ImageUrl("https://example.com/public.png"), "after"],
                ImageUrl("https://example.com/private.png"),
            ],
            metadata={"a13n.tool-content": {"result_index": 0, "items": [{"display": False}]}},
        )
    )
    restored = TranscriptPart.model_validate_json(part.model_dump_json())
    assert restored.content_parts is not None
    assert [item["type"] for item in restored.content_parts] == ["text", "image", "text"]
    assert restored.content_parts[1]["source"]["value"] == "https://example.com/public.png"
    assert "private.png" not in restored.model_dump_json()
