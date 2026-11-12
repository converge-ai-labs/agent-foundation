from datetime import UTC, datetime

from a13n_harness.compaction import CompactionInput, CompactionResult, apply_compaction
from a13n_harness_ui.thread_branch import Boundary, boundaries, branch_prefix
from pydantic_ai.messages import ModelRequest, ModelResponse, SystemPromptPart, TextPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters


def test_edit_uses_the_input_boundary_after_an_interrupted_reply():
    history = (
        ModelRequest(parts=[SystemPromptPart("Setup")]),
        ModelRequest(parts=[UserPromptPart("First")], run_id="first", timestamp=None),
        ModelResponse(parts=[TextPart("Partial")], state="interrupted"),
        ModelRequest(parts=[UserPromptPart("Try again")], run_id="retry"),
        ModelResponse(parts=[TextPart("Done")]),
    )
    points = boundaries(history, continuing_turn=False)
    assert points["first"] == points["entry:1"] == Boundary(before=1, after=None)
    assert points["retry"] == Boundary(before=3, after=5)
    edited, _, _ = branch_prefix(history, points["retry"].before)
    assert edited.message_history == history[:3]


def test_compaction_replay_does_not_hide_the_next_original_input():
    old = ModelRequest(parts=[UserPromptPart("Original")], run_id="old", timestamp=datetime(2020, 1, 1, tzinfo=UTC))
    history = [old, ModelResponse(parts=[TextPart("Answer")])]
    compact = CompactionInput.create(
        operation_id="compact",
        thread_id="thread",
        run_id="old",
        model_name="test",
        provider_name="function",
        messages=history,
        retained_requests=[old],
        parameters=ModelRequestParameters(),
    )
    restored = apply_compaction(
        CompactionResult.from_summary(compact.basis, "Summary"), thread_id="thread", messages=history
    )
    restored.append(ModelResponse(parts=[TextPart("Resumed")]))
    fresh = ModelRequest(parts=[UserPromptPart("Next")], run_id="fresh", timestamp=datetime.now(UTC))
    messages = (*restored, fresh, ModelResponse(parts=[TextPart("Done")]))
    points = boundaries(messages, continuing_turn=False)
    assert "old" not in points
    assert points["fresh"] == Boundary(before=len(restored), after=len(messages))
