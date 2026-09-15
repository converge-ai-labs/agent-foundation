"""Bounded root-operation notices, derived from actual output rather than a model call."""

from __future__ import annotations

import re
from html import unescape

from a13n_harness.capabilities import AskUserQuestionRequest
from pydantic import ValidationError
from pydantic_ai.tools import DeferredToolRequests

from a13n_harness_ui.conversation import excerpt_text
from a13n_harness_ui.live import RootOperationNotice
from a13n_harness_ui.surfaces import RootOperationStatus, RootOperationView


def reply_brief(text: str) -> str:
    """Preview the first prose block, omitting fences, headings and common markup."""
    # This is a display excerpt, not a Markdown renderer or a semantic summary.
    text = text[:8192]
    text = re.sub(r"(?ms)^\s*(`{3,}|~{3,})[^\n]*\n.*?(?:^\s*\1[^\n]*$|\Z)", "", text)
    blocks = re.split(r"\n\s*\n", text)
    for block in blocks:
        lines = [line for line in block.splitlines() if not re.match(r"^\s*(#{1,6}\s|[-*_]{3,}\s*$)", line)]
        value = " ".join(lines)
        value = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", value)
        value = re.sub(r"<[^>]+>", "", value)
        value = re.sub(r"(^|\s)(?:[-*+] |\d+\. |> )", r"\1", value)
        value = value.replace("**", "").replace("__", "").replace("`", "")
        value = excerpt_text(unescape(value), 320)
        if value:
            return value
    return ""


def root_operation_notice(
    operation: RootOperationView, *, output: str | None = None, deferred: DeferredToolRequests | None = None
) -> RootOperationNotice | None:
    status = operation.status
    if status not in {RootOperationStatus.completed, RootOperationStatus.failed, RootOperationStatus.suspended}:
        return None
    brief = ""
    if status is RootOperationStatus.completed:
        brief = reply_brief(output) if output else ""
    elif status is RootOperationStatus.failed:
        failure = operation.failure
        if operation.outcome is not None:
            failure = failure or operation.outcome.continuation.failure or operation.outcome.execution.failure
        if failure is not None:
            # Diagnostic report links follow the user-facing first line.
            brief = excerpt_text(failure.message.partition("\n")[0], 320)
    elif deferred is not None:
        for request in deferred.calls:
            if request.tool_name == "ask_user_question":
                try:
                    questions = AskUserQuestionRequest.model_validate(request.args_as_dict())
                except (ValueError, ValidationError):
                    continue
                brief = excerpt_text(" ".join(item.question for item in questions.questions), 320)
                break
        if not brief and deferred.approvals:
            request = deferred.approvals[0]
            metadata = deferred.metadata.get(request.tool_call_id, {})
            reason = metadata.get("reason") if isinstance(metadata, dict) else None
            brief = excerpt_text(
                f"Approve {request.tool_name}" + (f": {reason}" if isinstance(reason, str) else ""), 320
            )
        if not brief and deferred.calls:
            brief = excerpt_text(f"Provide a result for {deferred.calls[0].tool_name}.", 320)
    fallback = {
        RootOperationStatus.completed: "The agent finished this turn. Open the conversation to review the result.",
        RootOperationStatus.failed: "The operation could not complete. Open the conversation for details.",
        RootOperationStatus.suspended: "The agent needs your input before it can continue.",
    }
    notice_status = (
        "completed"
        if status is RootOperationStatus.completed
        else "failed"
        if status is RootOperationStatus.failed
        else "suspended"
    )
    return RootOperationNotice(
        receipt_id=operation.receipt.receipt_id, status=notice_status, brief=brief or fallback[status]
    )
