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
@pytest.mark.parametrize("content", ["plain result", None, {"ok": True}, "x" * (128 * 1024)])
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
