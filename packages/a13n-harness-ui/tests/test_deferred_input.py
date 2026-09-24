"""Host checkpoint serialization preserves native accepted facts, not replay grants."""

from datetime import UTC, datetime

import pytest
from a13n_harness import DeferredToolResume, HarnessState
from a13n_harness_ui.storage.contracts import (
    CompactChildDisplay,
    StoredChildCheckpoint,
    StoredContinuation,
    StoredDeferredInput,
)
from a13n_harness_ui.storage.objects import ObjectKind, ObjectRef
from pydantic_ai import ToolApproved, ToolDenied, ToolFailed, ToolReturn
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults


@pytest.mark.parametrize("child", [False, True])
@pytest.mark.parametrize(
    "value",
    [
        {"kind": "tool-failed", "message": "ordinary JSON"},
        ToolFailed("external failure"),
        ToolReturn({"ok": True}, content="explanation", metadata={"source": "client"}),
    ],
)
@pytest.mark.parametrize("approval", [ToolApproved(override_args={"value": 2}), ToolDenied("declined")])
def test_checkpoint_roundtrip_preserves_native_accepted_input(tmp_path, child, value, approval):
    calls = [ToolCallPart("lookup", {}, "call_external"), ToolCallPart("change", {"value": 1}, "call_local")]
    state = HarnessState.new(thread_id="thread_test", message_history=[ModelResponse(parts=calls)])
    accepted = DeferredToolResume(
        DeferredToolRequests(calls=calls[:1], approvals=calls[1:]),
        DeferredToolResults(
            calls={"call_external": value},
            approvals={"call_local": approval},
            metadata={"call_external": {"accepted": True}},
        ),
    )
    fields = dict(
        harness_release="0.0.0",
        run_composition=ObjectRef(
            object_kind=ObjectKind.run_composition, object_schema_version="1", logical_digest="0" * 64
        ),
        harness_state=state,
        accepted_input=StoredDeferredInput.capture(accepted, state),
        created_at=datetime.now(UTC),
    )
    if child:
        checkpoint = StoredChildCheckpoint(
            **fields,
            execution_id="execution_test",
            child_thread_id=state.thread_id,
            child_run_id="run_test",
            segment_index=0,
            display=CompactChildDisplay(),
            terminal=True,
        )
    else:
        checkpoint = StoredContinuation(**fields)
    path = tmp_path / "checkpoint.json"
    path.write_text(checkpoint.model_dump_json())
    restored = type(checkpoint).model_validate_json(path.read_text())
    assert restored.accepted_input is not None
    recovered = restored.accepted_input.recover()
    assert recovered.recovery
    assert recovered.results.to_tool_call_results() == accepted.results.to_tool_call_results()
    assert recovered.results.metadata == accepted.results.metadata
    assert recovered.requests == accepted.requests

    incorporated = HarnessState.new(
        thread_id=state.thread_id,
        message_history=[
            *state.message_history,
            ModelRequest(parts=[ToolReturnPart("lookup", "known", "call_external")]),
        ],
    )
    retained = StoredDeferredInput.capture(recovered, incorporated)
    assert retained is not None and retained.results.calls == {}
    completed = HarnessState.new(
        thread_id=state.thread_id,
        message_history=[
            *incorporated.message_history,
            ModelRequest(parts=[ToolReturnPart("change", "closed", "call_local")]),
        ],
    )
    assert StoredDeferredInput.capture(retained.recover(), completed) is None
