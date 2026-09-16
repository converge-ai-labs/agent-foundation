"""Explicit failed responses for Host-owned interaction expiry."""

from pydantic_ai import DeferredToolRequests

from a13n_harness_ui.surfaces import ApprovalDecision, ExternalToolResult, ThreadDeferredResponse

QUESTION_TIMEOUT_MESSAGE = (
    "The user did not respond before this clarification timed out. "
    "Do not wait for or repeat the same question. Continue with reasonable assumptions where possible. "
    "No answer or approval was provided."
)
INTERACTION_TIMEOUT_MESSAGE = (
    "The user did not respond before this interaction timed out. No approval or result was supplied."
)


def timeout_response(continuation_id: str, requests: DeferredToolRequests) -> ThreadDeferredResponse:
    """Resolve the complete request set without fabricating an answer or approval."""
    return ThreadDeferredResponse(
        expected_continuation_id=continuation_id,
        responses=tuple(
            ExternalToolResult(
                request_id=request.tool_call_id,
                denied=True,
                denial_message=QUESTION_TIMEOUT_MESSAGE
                if request.tool_name == "ask_user_question"
                else INTERACTION_TIMEOUT_MESSAGE,
            )
            for request in requests.calls
        )
        + tuple(
            ApprovalDecision(
                request_id=request.tool_call_id,
                approved=False,
                denial_message=INTERACTION_TIMEOUT_MESSAGE,
            )
            for request in requests.approvals
        ),
    )
