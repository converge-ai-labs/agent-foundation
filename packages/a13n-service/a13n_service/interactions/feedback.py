"""Map accepted waiting feedback into exact native deferred results."""

from __future__ import annotations

from a13n_harness import DeferredToolResume
from pydantic import TypeAdapter, ValidationError
from pydantic_ai import ToolApproved, ToolDenied
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults

from .control_domain import AcceptedPendingResolution, PendingResolutionOutcome, WaitingRunFeedback
from .domain import PendingCallKind
from .state import RunCheckpoint, WaitingOutcomeCandidate

_REQUESTS = TypeAdapter(DeferredToolRequests)
_NO_CLIENT_TOOL_RESPONSE = "The external client supplied no response."
_NO_USER_RESPONSE = "The user supplied no response."


class WaitingFeedbackMappingError(ValueError):
    """Accepted feedback does not match its exact sealed waiting state."""


def map_waiting_feedback(
    feedback: WaitingRunFeedback,
    parent: RunCheckpoint,
) -> DeferredToolResume:
    """Construct one complete native result map in frozen pending order."""

    candidate = parent.outcome_candidate
    deferred = parent.host.deferred
    if parent.checkpoint_kind != "waiting" or not isinstance(candidate, WaitingOutcomeCandidate) or deferred is None:
        raise WaitingFeedbackMappingError("feedback parent is not a complete waiting state")
    if feedback.waiting_run_id != parent.run_id:
        raise WaitingFeedbackMappingError("feedback waiting Run does not match its parent state")
    if tuple(item.call_id for item in feedback.resolutions) != tuple(item.call_id for item in candidate.pending.calls):
        raise WaitingFeedbackMappingError("feedback does not exactly cover the frozen pending order")
    try:
        requests = _REQUESTS.validate_python(deferred.requests)
    except ValidationError as error:
        raise WaitingFeedbackMappingError("waiting state contains invalid native requests") from error

    approvals: dict[str, bool | ToolApproved | ToolDenied] = {}
    calls: dict[str, object] = {}
    for resolution in feedback.resolutions:
        if resolution.kind is PendingCallKind.approval:
            approvals[resolution.call_id] = _approval_result(resolution)
        elif resolution.kind is PendingCallKind.client_tool:
            calls[resolution.call_id] = _client_tool_result(resolution.outcome, resolution.result)
        else:
            calls[resolution.call_id] = _user_input_result(resolution.outcome, resolution.result)
    if set(approvals) != {request.tool_call_id for request in requests.approvals}:
        raise WaitingFeedbackMappingError("feedback approval category does not match native requests")
    if set(calls) != {request.tool_call_id for request in requests.calls}:
        raise WaitingFeedbackMappingError("feedback call category does not match native requests")
    return DeferredToolResume(
        requests=requests,
        results=DeferredToolResults(approvals=approvals, calls=calls),
    )


def _approval_result(resolution: AcceptedPendingResolution) -> ToolApproved | ToolDenied:
    if resolution.outcome is PendingResolutionOutcome.approve:
        return ToolApproved()
    if resolution.outcome is PendingResolutionOutcome.reject:
        return ToolDenied(resolution.reason or "Approval was denied.")
    raise WaitingFeedbackMappingError("approval feedback has an incompatible outcome")


def _client_tool_result(outcome: PendingResolutionOutcome, result: object) -> object:
    if outcome is PendingResolutionOutcome.complete:
        return result
    if outcome is PendingResolutionOutcome.no_response:
        return ToolFailed(_NO_CLIENT_TOOL_RESPONSE)
    raise WaitingFeedbackMappingError("client-tool feedback has an incompatible outcome")


def _user_input_result(outcome: PendingResolutionOutcome, result: object) -> object:
    if outcome is PendingResolutionOutcome.respond:
        return result
    if outcome is PendingResolutionOutcome.no_response:
        return {"answers": {}, "response": _NO_USER_RESPONSE}
    raise WaitingFeedbackMappingError("user-input feedback has an incompatible outcome")


__all__ = ["WaitingFeedbackMappingError", "map_waiting_feedback"]
