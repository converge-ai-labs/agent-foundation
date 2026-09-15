from __future__ import annotations

from typing import Literal

import pytest
from a13n_harness_ui.surfaces import TranscriptPart
from a13n_harness_ui.thread_projection import _request_parts
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
