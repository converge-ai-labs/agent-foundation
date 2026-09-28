"""Authoritative correlation envelope for native deferred-tool continuation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace

from pydantic import JsonValue, TypeAdapter
from pydantic_ai import ToolApproved, ToolDenied, ToolFailed, ToolReturn
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults

from a13n_harness._json import dump_json_bytes, require_finite_json
from a13n_harness.errors import RunError
from a13n_harness.state import HarnessState

MAX_DEFERRED_ITEMS = 128
MAX_DEFERRED_METADATA_BYTES = 64 * 1024
DEFERRED_PRESENTATION_KEY = "a13n.interaction.presentation"
MAX_PRESENTATION_BYTES = 16 * 1024
_PRESENTATION = TypeAdapter(dict[str, JsonValue])


def deferred_presentation(metadata: Mapping[str, object]) -> dict[str, JsonValue] | None:
    """Opt-in public display data. Other deferred metadata remains private to execution."""
    value = metadata.get(DEFERRED_PRESENTATION_KEY)
    if value is None:
        return None
    presentation = _PRESENTATION.validate_python(value, strict=True)
    require_finite_json(presentation)
    if len(dump_json_bytes(presentation)) > MAX_PRESENTATION_BYTES:
        raise ValueError("Deferred presentation is too large")
    return deepcopy(presentation)


@dataclass(frozen=True, slots=True)
class DeferredToolResume:
    """Detached pending requests paired with their complete native result batch."""

    requests: DeferredToolRequests
    results: DeferredToolResults
    # Recovery carries accepted facts, never permission to replay an old approval.
    recovery: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.requests, DeferredToolRequests) or not isinstance(self.results, DeferredToolResults):
            raise TypeError("DeferredToolResume requires native DeferredToolRequests and DeferredToolResults")
        try:
            object.__setattr__(self, "requests", deepcopy(self.requests))
            object.__setattr__(self, "results", deepcopy(self.results))
        except Exception as exc:
            raise TypeError("Deferred tool values must be detachable") from exc

    def remaining(self, messages: Sequence[ModelMessage]) -> DeferredToolResume | None:
        """Keep only this batch's unanswered calls at the continuation tail.

        Hosts retain accepted input alongside their checkpoint until native history
        incorporates it. A later response (including compacted history) has already
        passed this batch; it must not receive the original results again.
        """
        response_index = next(
            (index for index in range(len(messages) - 1, -1, -1) if isinstance(messages[index], ModelResponse)),
            None,
        )
        if response_index is None:
            raise RunError("Deferred input has no response history.", code="deferred_request_missing")
        response = messages[response_index]
        assert isinstance(response, ModelResponse)
        ids = {call.tool_call_id for call in response.tool_calls}
        completed = {
            part.tool_call_id
            for message in messages[response_index + 1 :]
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart | RetryPromptPart)
        }
        calls = [call for call in self.requests.calls if call.tool_call_id in ids - completed]
        approvals = [call for call in self.requests.approvals if call.tool_call_id in ids - completed]
        if not calls and not approvals:
            return None
        pending = {call.tool_call_id for call in (*calls, *approvals)}
        return DeferredToolResume(
            DeferredToolRequests(
                calls=calls,
                approvals=approvals,
                metadata={key: value for key, value in self.requests.metadata.items() if key in pending},
            ),
            DeferredToolResults(
                calls={call.tool_call_id: self.results.calls[call.tool_call_id] for call in calls},
                approvals={call.tool_call_id: self.results.approvals[call.tool_call_id] for call in approvals},
                metadata={key: value for key, value in self.results.metadata.items() if key in pending},
            ),
            recovery=self.recovery,
        )


def preflight_deferred_resume(
    resume: DeferredToolResume,
    *,
    previous_state: HarnessState | None,
) -> DeferredToolResume | None:
    """Reject incomplete, misplaced, stale, or message-mismatched deferred results."""
    if not isinstance(resume, DeferredToolResume):
        raise RunError("deferred_resume must be DeferredToolResume.", code="deferred_resume_invalid")
    if previous_state is None:
        raise RunError("Deferred resume requires previous_state.", code="deferred_state_required")

    if resume.recovery:
        remaining = resume.remaining(previous_state.message_history)
        if remaining is None:
            return None
        resume = remaining
    detached = DeferredToolResume(resume.requests, resume.results, recovery=resume.recovery)
    call_ids, approval_ids = validate_deferred_requests(detached.requests)

    if set(detached.results.calls) != call_ids or set(detached.results.approvals) != approval_ids:
        raise RunError(
            "Deferred results must exactly cover their request categories.",
            code="deferred_results_incomplete",
        )
    for approval in detached.results.approvals.values():
        if type(approval) is not bool and not isinstance(approval, ToolApproved | ToolDenied):
            raise RunError(
                "Deferred approval results must use the native approval value types.",
                code="deferred_approval_invalid",
            )
    try:
        pending_calls = {request.tool_call_id: request for request in detached.requests.calls}
        for call_id, call_result in tuple(detached.results.calls.items()):
            request = pending_calls[call_id]
            if isinstance(call_result, ToolFailed):
                require_finite_json(call_result.message)
                continue
            if request.tool_name == "ask_user_question":
                from a13n_harness.toolsets.interaction import validate_user_question_result

                if isinstance(call_result, ToolReturn):
                    call_result = replace(
                        call_result,
                        return_value=validate_user_question_result(request.args_as_dict(), call_result.return_value),
                    )
                else:
                    call_result = validate_user_question_result(request.args_as_dict(), call_result)
                detached.results.calls[call_id] = call_result
            if isinstance(call_result, ToolReturn):
                require_finite_json(call_result.return_value)
                require_finite_json(call_result.metadata)
            else:
                require_finite_json(call_result)
        for approval in detached.results.approvals.values():
            if isinstance(approval, ToolApproved) and approval.override_args is not None:
                require_finite_json(approval.override_args)
    except (RecursionError, ValueError) as exc:
        raise RunError("Deferred results contain invalid JSON values.", code="deferred_results_invalid") from exc
    _validate_metadata(detached.results.metadata, call_ids | approval_ids)

    pending = {part.tool_call_id: part for part in (*detached.requests.calls, *detached.requests.approvals)}
    completed: set[str] = set()
    last_response: ModelResponse | None = None
    for message in previous_state.message_history:
        if isinstance(message, ModelResponse):
            last_response = message
            completed.clear()
        elif isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart | RetryPromptPart):
                    completed.add(part.tool_call_id)
    if last_response is None:
        raise RunError(
            "Prior message history has no response containing the pending calls.",
            code="deferred_request_missing",
        )
    response_calls = [part for part in last_response.parts if isinstance(part, ToolCallPart)]
    response_ids = [part.tool_call_id for part in response_calls]
    if len(set(response_ids)) != len(response_ids):
        raise RunError("Prior message history has duplicate call IDs.", code="deferred_request_duplicate")
    history_calls = {part.tool_call_id: part for part in response_calls}

    for call_id, request in pending.items():
        history = history_calls.get(call_id)
        if history is None:
            raise RunError(
                "A pending deferred request is absent from prior message history.",
                code="deferred_request_missing",
                details={"tool_call_id": call_id},
            )
        if call_id in completed:
            raise RunError(
                "A pending deferred request was already integrated.",
                code="deferred_request_completed",
                details={"tool_call_id": call_id},
            )
        try:
            matches = history.tool_name == request.tool_name and history.args_as_dict() == request.args_as_dict()
        except ValueError as exc:
            raise RunError(
                "A pending deferred request has invalid arguments.",
                code="deferred_request_invalid",
            ) from exc
        if not matches:
            raise RunError(
                "A pending deferred request does not match prior message history.",
                code="deferred_request_mismatch",
                details={"tool_call_id": call_id},
            )
    return detached


def validate_deferred_requests(requests: DeferredToolRequests) -> tuple[set[str], set[str]]:
    """Validate the shared bounded batch shape for live resumes and retained facts."""
    call_ids = _request_ids(requests.calls, category="calls")
    approval_ids = _request_ids(requests.approvals, category="approvals")
    if call_ids & approval_ids:
        raise RunError(
            "Deferred request categories must not overlap.",
            code="deferred_request_category_overlap",
        )
    if len(call_ids) + len(approval_ids) == 0:
        raise RunError("Deferred resume has no pending requests.", code="deferred_requests_empty")
    if len(call_ids) + len(approval_ids) > MAX_DEFERRED_ITEMS:
        raise RunError("Deferred resume has too many pending requests.", code="deferred_requests_too_large")

    _validate_metadata(requests.metadata, call_ids | approval_ids)
    return call_ids, approval_ids


def _request_ids(requests: list[ToolCallPart], *, category: str) -> set[str]:
    ids: set[str] = set()
    for request in requests:
        if not isinstance(request, ToolCallPart) or not request.tool_call_id or request.tool_call_id in ids:
            raise RunError(
                "Deferred request IDs must be non-blank and unique within each category.",
                code="deferred_request_duplicate",
                details={"category": category},
            )
        ids.add(request.tool_call_id)
    return ids


def _validate_metadata(metadata: dict[str, dict[str, object]], pending_ids: set[str]) -> None:
    if not set(metadata) <= pending_ids:
        raise RunError("Deferred metadata references an unknown call.", code="deferred_metadata_invalid")
    try:
        for value in metadata.values():
            deferred_presentation(value)
        encoded = dump_json_bytes(metadata, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise RunError("Deferred metadata is not JSON-safe.", code="deferred_metadata_invalid") from exc
    if len(encoded) > MAX_DEFERRED_METADATA_BYTES:
        raise RunError("Deferred metadata is too large.", code="deferred_metadata_invalid")
