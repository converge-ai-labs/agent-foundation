"""The Harness deferred-tool contract: what a suspended run waits for, and the resume that answers it.

The native requests are stored unchanged in the waiting run's checkpoint, because the Harness re-reads their
metadata on resume. The public `Pending` projection is derived from them once, when the run suspends.
"""

from a13n_harness import DeferredToolResume
from a13n_harness.tools.approval import APPROVAL_PRESENTATION_KEY
from a13n_harness.tools.deferred import deferred_presentation
from pydantic import JsonValue, TypeAdapter
from pydantic_ai import ToolDenied, ToolFailed
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults

from a13n_service.runs.schemas import Approve, Deny, Failed, Pending, PendingCall, Resume, Returned

_REQUESTS = TypeAdapter(DeferredToolRequests)


def dump(requests: DeferredToolRequests) -> JsonValue:
    return _REQUESTS.dump_python(requests, mode="json")


def load(value: JsonValue) -> DeferredToolRequests:
    return _REQUESTS.validate_python(value)


def pending(requests: DeferredToolRequests) -> Pending:
    """Expose only intentionally public presentation, never the entire native metadata."""

    def project(part: ToolCallPart, *, approval: bool = False) -> PendingCall:
        metadata = requests.metadata.get(part.tool_call_id, {})
        presentation = deferred_presentation(metadata)
        if presentation is None and approval:
            presentation = metadata.get(APPROVAL_PRESENTATION_KEY)
        return PendingCall.model_validate(
            {
                "tool_call_id": part.tool_call_id,
                "tool_name": part.tool_name,
                "arguments": part.args_as_dict(),
                "presentation": presentation,
            }
        )

    return Pending(
        approvals=tuple(project(part, approval=True) for part in requests.approvals),
        calls=tuple(project(part) for part in requests.calls),
    )


def fork_results(pending: Pending) -> Resume:
    """Fork abandons inherited waits in the new branch only; this is not a resume omission policy."""
    return Resume(
        approvals={
            item.tool_call_id: Deny(action="deny", reason="No decision was given") for item in pending.approvals
        },
        calls={item.tool_call_id: Failed(status="failed", message="No response was given") for item in pending.calls},
    )


def resume(requests: DeferredToolRequests, answers: Resume) -> DeferredToolResume:
    results = DeferredToolResults(
        approvals={
            call_id: True if isinstance(answer, Approve) else ToolDenied(answer.reason or "The call was rejected")
            for call_id, answer in answers.approvals.items()
        },
        calls={
            call_id: answer.value if isinstance(answer, Returned) else ToolFailed(answer.message)
            for call_id, answer in answers.calls.items()
        },
    )
    return DeferredToolResume(requests=requests, results=results)
