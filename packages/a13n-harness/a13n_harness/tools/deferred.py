"""Authoritative correlation envelope for native deferred-tool continuation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic_ai import ToolApproved, ToolDenied, ToolFailed, ToolReturn
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.tools import DeferredToolApprovalResult
from pydantic_ai.tools import DeferredToolRequests as NativeDeferredToolRequests
from pydantic_ai.tools import DeferredToolResults as DeferredToolResults

from a13n_harness._json import dump_json_bytes, require_finite_json
from a13n_harness.errors import RunError
from a13n_harness.state import HarnessState
from a13n_harness.tools._output import _render_tool_return

MAX_DEFERRED_ITEMS = 128
MAX_DEFERRED_METADATA_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class DeferredToolCall:
    """A model call awaiting one actual tool invocation, without execution authority."""

    model_call: ToolCallPart
    target_call: ToolCallPart
    target_identity: str

    @property
    def forwarded(self) -> bool:
        return self.model_call.tool_call_id != self.target_call.tool_call_id


@dataclass(kw_only=True)
class DeferredToolRequests:
    """Portable waits, with replies and metadata keyed by the actual target call ID."""

    calls: list[DeferredToolCall] = field(default_factory=list)
    approvals: list[DeferredToolCall] = field(default_factory=list)
    metadata: dict[str, dict[str, Any]] = field(default_factory=dict)

    def target_requests(self) -> NativeDeferredToolRequests:
        """Project actual targets for native handlers and approval observations."""
        return NativeDeferredToolRequests(
            calls=[item.target_call for item in self.calls],
            approvals=[item.target_call for item in self.approvals],
            metadata=deepcopy(self.metadata),
        )

    def build_results(
        self,
        *,
        approvals: Mapping[str, bool | DeferredToolApprovalResult] | None = None,
        calls: dict[str, Any] | None = None,
        metadata: dict[str, dict[str, Any]] | None = None,
        approve_all: bool = False,
    ) -> DeferredToolResults:
        return self.target_requests().build_results(
            approvals=approvals, calls=calls, metadata=metadata, approve_all=approve_all
        )


@dataclass(frozen=True, slots=True)
class DeferredToolResume:
    """Detached pending requests paired with their complete native result batch."""

    requests: DeferredToolRequests
    results: DeferredToolResults
    # Recovery carries accepted facts, never permission to replay an old approval.
    recovery: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.requests, DeferredToolRequests) or not isinstance(self.results, DeferredToolResults):
            raise TypeError("DeferredToolResume requires Harness DeferredToolRequests and native DeferredToolResults")
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
        calls = [call for call in self.requests.calls if call.model_call.tool_call_id in ids - completed]
        approvals = [call for call in self.requests.approvals if call.model_call.tool_call_id in ids - completed]
        if not calls and not approvals:
            return None
        pending = {call.target_call.tool_call_id for call in (*calls, *approvals)}
        return DeferredToolResume(
            DeferredToolRequests(
                calls=calls,
                approvals=approvals,
                metadata={key: value for key, value in self.requests.metadata.items() if key in pending},
            ),
            DeferredToolResults(
                calls={
                    call.target_call.tool_call_id: self.results.calls[call.target_call.tool_call_id] for call in calls
                },
                approvals={
                    call.target_call.tool_call_id: self.results.approvals[call.target_call.tool_call_id]
                    for call in approvals
                },
                metadata={key: value for key, value in self.results.metadata.items() if key in pending},
            ),
            recovery=self.recovery,
        )

    def model_results(self) -> DeferredToolResults:
        """Adapt replies to the native history boundary; target approvals stay target-local."""
        results = DeferredToolResults()
        for item in self.requests.calls:
            results.calls[item.model_call.tool_call_id] = self.results.calls[item.target_call.tool_call_id]
        for item in self.requests.approvals:
            decision = self.results.approvals[item.target_call.tool_call_id]
            # An override applies to the target's arguments, never the proxy envelope.
            if item.forwarded and isinstance(decision, ToolApproved):
                decision = ToolApproved()
            results.approvals[item.model_call.tool_call_id] = decision
        for item in (*self.requests.calls, *self.requests.approvals):
            if item.target_call.tool_call_id in self.results.metadata:
                results.metadata[item.model_call.tool_call_id] = deepcopy(
                    self.results.metadata[item.target_call.tool_call_id]
                )
        return results


@dataclass(slots=True)
class DeferredInputState:
    """Run-owned, monotonically shrinking accepted facts; never a replay grant."""

    pending: DeferredToolResume | None = None
    # Current-step associations are observations, not another executable registry.
    calls: dict[str, DeferredToolCall] = field(default_factory=dict)
    metadata: dict[str, dict[str, Any]] = field(default_factory=dict)

    def record_metadata(self, call_id: str, metadata: Mapping[str, Any]) -> None:
        """Merge handler metadata without replacing the captured function identity."""
        captured = self.metadata.setdefault(call_id, {})
        function_id = captured.get("a13n.harness.deferred-function-id")
        captured.update(deepcopy(metadata))
        if function_id is not None:
            captured["a13n.harness.deferred-function-id"] = function_id

    def reconcile(self, messages: Sequence[ModelMessage]) -> None:
        if self.pending is not None:
            self.pending = self.pending.remaining(messages)

    def export(self, requests: NativeDeferredToolRequests) -> DeferredToolRequests:
        """Detach the final native pending batch with its captured target associations."""
        metadata: dict[str, dict[str, Any]] = {}

        def entries(parts: list[ToolCallPart], *, approval: bool = False) -> list[DeferredToolCall]:
            result = []
            for part in parts:
                item = self.calls.get(part.tool_call_id)
                if item is None:
                    raise RunError("Deferred call has no captured identity.", code="deferred_request_missing")
                if not item.forwarded:
                    item = replace(item, model_call=part, target_call=part)
                target_id = item.target_call.tool_call_id
                metadata[target_id] = {
                    **requests.metadata.get(part.tool_call_id, {}),
                    **self.metadata.get(target_id, {}),
                }
                if approval:
                    metadata[target_id].pop("a13n.harness.deferred-function-id", None)
                result.append(deepcopy(item))
            return result

        result = DeferredToolRequests(
            calls=entries(requests.calls), approvals=entries(requests.approvals, approval=True), metadata=metadata
        )
        validate_deferred_requests(result)
        return deepcopy(result)


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
    from a13n_harness.tools.client import client_tool_name

    try:
        pending_calls = {item.target_call.tool_call_id: item.target_call for item in detached.requests.calls}
        for call_id, call_result in tuple(detached.results.calls.items()):
            request = pending_calls[call_id]
            if isinstance(call_result, ToolFailed):
                require_finite_json(call_result.message)
                continue
            if client_tool_name(request, detached.requests.metadata.get(call_id, {})) == "ask_user_question":
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
                detached.results.calls[call_id] = _render_tool_return(call_result)
            else:
                require_finite_json(call_result)
        for approval in detached.results.approvals.values():
            if isinstance(approval, ToolApproved) and approval.override_args is not None:
                require_finite_json(approval.override_args)
    except (RecursionError, ValueError) as exc:
        raise RunError("Deferred results contain invalid JSON values.", code="deferred_results_invalid") from exc
    _validate_metadata(detached.results.metadata, call_ids | approval_ids)

    pending = {
        item.model_call.tool_call_id: item.model_call
        for item in (*detached.requests.calls, *detached.requests.approvals)
    }
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

    model_ids: set[str] = set()
    for item in (*requests.calls, *requests.approvals):
        model, target = item.model_call, item.target_call
        if not model.tool_call_id or model.tool_call_id in model_ids:
            raise RunError("Deferred model call IDs must be unique.", code="deferred_request_duplicate")
        model_ids.add(model.tool_call_id)
        if not isinstance(item.target_identity, str) or not item.target_identity or len(item.target_identity) > 1024:
            raise RunError("Deferred target identity is invalid.", code="deferred_request_invalid")
        try:
            require_finite_json(model.args_as_dict())
            require_finite_json(target.args_as_dict())
            if not model.tool_name or not target.tool_name or (not item.forwarded and model != target):
                raise ValueError("Inconsistent direct call")
        except (TypeError, ValueError, RecursionError) as exc:
            raise RunError("Deferred call association is invalid.", code="deferred_request_invalid") from exc

    # A forwarded target cannot impersonate another model call in the same batch.
    if any(
        item.forwarded and item.target_call.tool_call_id in model_ids for item in (*requests.calls, *requests.approvals)
    ):
        raise RunError("Deferred target IDs overlap model calls.", code="deferred_request_duplicate")

    _validate_metadata(requests.metadata, call_ids | approval_ids)
    return call_ids, approval_ids


def _request_ids(requests: list[DeferredToolCall], *, category: str) -> set[str]:
    ids: set[str] = set()
    for request in requests:
        if (
            not isinstance(request, DeferredToolCall)
            or not isinstance(request.target_call, ToolCallPart)
            or not isinstance(request.model_call, ToolCallPart)
            or not request.target_call.tool_call_id
            or request.target_call.tool_call_id in ids
        ):
            raise RunError(
                "Deferred request IDs must be non-blank and unique within each category.",
                code="deferred_request_duplicate",
                details={"category": category},
            )
        ids.add(request.target_call.tool_call_id)
    return ids


def _validate_metadata(metadata: dict[str, dict[str, object]], pending_ids: set[str]) -> None:
    if not set(metadata) <= pending_ids:
        raise RunError("Deferred metadata references an unknown call.", code="deferred_metadata_invalid")
    try:
        encoded = dump_json_bytes(metadata, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise RunError("Deferred metadata is not JSON-safe.", code="deferred_metadata_invalid") from exc
    if len(encoded) > MAX_DEFERRED_METADATA_BYTES:
        raise RunError("Deferred metadata is too large.", code="deferred_metadata_invalid")
