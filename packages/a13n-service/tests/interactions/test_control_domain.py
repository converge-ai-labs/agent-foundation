from __future__ import annotations

import pytest
from a13n_service.interactions.control_domain import (
    AcceptedPendingResolution,
    CompletePendingResolution,
    ControlValidationError,
    PendingResolutionOutcome,
    RespondPendingResolution,
    ThreadRunSubmissionIntent,
    ThreadRunSubmissionRequest,
    normalize_feedback,
    normalize_waiting_continue,
)
from a13n_service.interactions.domain import PendingCallKind, PendingCallSummary, RunPendingSummary
from a13n_service.interactions.input import AcceptedAgentInput, AgentInput, TextContent
from pydantic import ValidationError


@pytest.mark.parametrize(
    "execution",
    [
        {"agent_id": "agent_1111111111111111"},
        {"agent_revision_id": "arev_1111111111111111"},
        {"expected_default_revision_id": "arev_1111111111111111"},
        {"environment": None},
        {"config_override": {"instructions": "Changed execution"}},
    ],
)
def test_waiting_continue_rejects_execution_selection_but_ordinary_submission_preserves_it(execution) -> None:
    body = {
        "expected_thread_version": 1,
        "input": {"schema_version": "1", "content": [{"type": "text", "text": "Continue"}]},
        **execution,
    }
    ordinary = ThreadRunSubmissionRequest.model_validate(body)
    assert execution.items() <= ordinary.intent().model_dump(mode="json", exclude_unset=True).items()
    with pytest.raises(ValidationError, match="Waiting Continue cannot supply execution overrides"):
        ThreadRunSubmissionRequest.model_validate(
            {**body, "waiting_resolution": {"mode": "defaults", "sealed_state_digest_sha256": "a" * 64}}
        )


def test_waiting_continue_without_execution_selection_preserves_input_and_hook_omission() -> None:
    request = ThreadRunSubmissionRequest.model_validate(
        {
            "expected_thread_version": 1,
            "input": {"schema_version": "1", "content": [{"type": "text", "text": "Continue"}]},
            "waiting_resolution": {"mode": "defaults", "sealed_state_digest_sha256": "a" * 64},
        }
    )
    assert request.input.content == (TextContent(text="Continue"),)
    assert "environment" not in request.model_fields_set and "hook_subscription" not in request.model_fields_set


def test_feedback_normalization_preserves_frozen_order_and_fails_closed() -> None:
    pending = RunPendingSummary(
        calls=(
            PendingCallSummary(call_id="approve-1", kind=PendingCallKind.approval),
            PendingCallSummary(call_id="tool-1", kind=PendingCallKind.client_tool),
            PendingCallSummary(call_id="question-1", kind=PendingCallKind.user_input),
        )
    )

    feedback = normalize_feedback(
        waiting_run_id="run_1111111111111111",
        sealed_state_digest_sha256="a" * 64,
        pending=pending,
        submitted=(
            CompletePendingResolution(call_id="tool-1", result={"answer": 42}),
            RespondPendingResolution(call_id="question-1", response=None),
        ),
    )

    assert tuple(item.call_id for item in feedback.resolutions) == ("approve-1", "tool-1", "question-1")
    assert tuple(item.outcome for item in feedback.resolutions) == (
        PendingResolutionOutcome.reject,
        PendingResolutionOutcome.complete,
        PendingResolutionOutcome.respond,
    )
    assert feedback.resolutions[2].result is None


def test_feedback_rejects_unknown_or_kind_incompatible_resolution() -> None:
    pending = RunPendingSummary(calls=(PendingCallSummary(call_id="approve-1", kind=PendingCallKind.approval),))

    with pytest.raises(ControlValidationError) as unknown:
        normalize_feedback(
            waiting_run_id="run_1111111111111111",
            sealed_state_digest_sha256="a" * 64,
            pending=pending,
            submitted=(CompletePendingResolution(call_id="missing", result=1),),
        )
    assert unknown.value.code == "feedback_call_unknown"

    with pytest.raises(ControlValidationError) as incompatible:
        normalize_feedback(
            waiting_run_id="run_1111111111111111",
            sealed_state_digest_sha256="a" * 64,
            pending=pending,
            submitted=(CompletePendingResolution(call_id="approve-1", result=1),),
        )
    assert incompatible.value.code == "feedback_action_invalid"


def test_queued_submission_intent_digest_is_canonical() -> None:
    first = ThreadRunSubmissionIntent(
        input=AgentInput(
            schema_version="1",
            content=(TextContent(text="hello"),),
            structured_content={"b": 2, "a": 1},
        )
    )
    second = ThreadRunSubmissionIntent.model_validate(
        {
            "input": {
                "structured_content": {"a": 1, "b": 2},
                "content": [{"text": "hello", "type": "text"}],
                "schema_version": "1",
            }
        }
    )

    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.digest_sha256() == second.digest_sha256()


def test_waiting_continue_carries_fail_closed_defaults_and_canonical_input() -> None:
    pending = RunPendingSummary(
        calls=(
            PendingCallSummary(call_id="approve-1", kind=PendingCallKind.approval),
            PendingCallSummary(call_id="tool-1", kind=PendingCallKind.client_tool),
        )
    )
    input = AcceptedAgentInput(schema_version="1", content=(TextContent(text="continue"),))

    continued = normalize_waiting_continue(
        waiting_run_id="run_1111111111111111",
        sealed_state_digest_sha256="a" * 64,
        pending=pending,
        input=input,
    )

    assert continued.input == input
    assert tuple(item.outcome for item in continued.resolutions) == (
        PendingResolutionOutcome.reject,
        PendingResolutionOutcome.no_response,
    )


def test_accepted_resolution_rejects_kind_outcome_mismatch() -> None:
    with pytest.raises(ValidationError, match="outcome does not match"):
        AcceptedPendingResolution(
            call_id="approval-1",
            kind=PendingCallKind.approval,
            outcome=PendingResolutionOutcome.complete,
            result={"unexpected": True},
        )
