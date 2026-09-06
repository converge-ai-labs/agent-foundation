from __future__ import annotations

from a13n_service.interactions.control_domain import (
    CompletePendingResolution,
    RespondPendingResolution,
    normalize_feedback,
)
from a13n_service.interactions.domain import (
    PendingCallKind,
    PendingCallSummary,
    RunPendingSummary,
    RunWaitReason,
)
from a13n_service.interactions.feedback import map_waiting_feedback
from a13n_service.interactions.state import (
    DeferredContinuationState,
    HostContinuationState,
    RunStateEnvelope,
    WaitingOutcomeCandidate,
)
from pydantic import TypeAdapter
from pydantic_ai import ToolDenied
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import DeferredToolRequests

from .conftest import ATTEMPT_ID, RUN_ID, initial_state


def test_waiting_feedback_maps_every_native_request_explicitly() -> None:
    parent, pending = _waiting_parent()
    feedback = normalize_feedback(
        waiting_run_id=RUN_ID,
        sealed_state_digest_sha256="a" * 64,
        pending=pending,
        submitted=(
            CompletePendingResolution(call_id="client-1", result={"ok": True}),
            RespondPendingResolution(
                call_id="question-1",
                response={"answers": {"Proceed?": "Yes"}},
            ),
        ),
    )

    resume = map_waiting_feedback(feedback, parent)

    assert isinstance(resume.results.approvals["approval-1"], ToolDenied)
    assert resume.results.calls["client-1"] == {"ok": True}
    assert resume.results.calls["question-1"] == {"answers": {"Proceed?": "Yes"}}
    assert set(resume.results.approvals) == {"approval-1"}
    assert set(resume.results.calls) == {"client-1", "question-1"}


def test_waiting_feedback_no_response_is_an_explicit_failed_or_empty_result() -> None:
    parent, pending = _waiting_parent()
    feedback = normalize_feedback(
        waiting_run_id=RUN_ID,
        sealed_state_digest_sha256="a" * 64,
        pending=pending,
        submitted=(),
    )

    resume = map_waiting_feedback(feedback, parent)

    assert isinstance(resume.results.calls["client-1"], ToolFailed)
    assert resume.results.calls["question-1"] == {
        "answers": {},
        "response": "The user supplied no response.",
    }


def _waiting_parent() -> tuple[RunStateEnvelope, RunPendingSummary]:
    requests = DeferredToolRequests(
        calls=[
            ToolCallPart(tool_name="client_action", args={}, tool_call_id="client-1"),
            ToolCallPart(
                tool_name="ask_user_question",
                args={
                    "questions": [
                        {
                            "question": "Proceed?",
                            "header": "Confirm",
                            "options": [
                                {"label": "Yes", "description": "Proceed."},
                                {"label": "No", "description": "Stop."},
                            ],
                        }
                    ]
                },
                tool_call_id="question-1",
            ),
        ],
        approvals=[ToolCallPart(tool_name="dangerous", args={}, tool_call_id="approval-1")],
    )
    pending = RunPendingSummary(
        calls=(
            PendingCallSummary(call_id="approval-1", kind=PendingCallKind.approval),
            PendingCallSummary(call_id="client-1", kind=PendingCallKind.client_tool),
            PendingCallSummary(call_id="question-1", kind=PendingCallKind.user_input),
        )
    )
    previous = initial_state()
    payload = previous.model_dump(mode="python", by_alias=True)
    payload.update(
        checkpoint_seq=1,
        checkpoint_kind="waiting",
        input_disposition="applied",
        last_checkpoint_run_attempt_id=ATTEMPT_ID,
        last_checkpoint_fence=1,
        writer_fence=1,
        host=HostContinuationState(
            deferred=DeferredContinuationState(
                requests=TypeAdapter(DeferredToolRequests).dump_python(requests, mode="json"),
                effective_client_tool_surface={"tools": ["client_action"]},
                effective_surface_digest_sha256="b" * 64,
            )
        ),
        outcome_candidate=WaitingOutcomeCandidate(
            wait_reason=RunWaitReason.multiple,
            pending=pending,
        ),
    )
    return RunStateEnvelope.model_validate(payload), pending
