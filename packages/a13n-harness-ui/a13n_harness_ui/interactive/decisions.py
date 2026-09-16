"""Presentation-only drafts for one exact App-owned continuation batch."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Literal

from a13n_harness_ui.interaction_timeout import INTERACTION_TIMEOUT_MESSAGE, QUESTION_TIMEOUT_MESSAGE
from a13n_harness_ui.surfaces import (
    ApprovalDecision,
    ApprovalRequestView,
    DecisionBatchView,
    DecisionRequestView,
    ExternalToolResult,
    StructuredQuestionRequestView,
    ThreadDeferredResponse,
)

from .selection import Choice, Selection, resolve_choice


@dataclass(slots=True)
class DecisionInteraction:
    batch: DecisionBatchView
    index: int = 0
    question_index: int = 0
    responses: list[ApprovalDecision | ExternalToolResult] = field(default_factory=list)
    answers: dict[str, str | tuple[str, ...]] = field(default_factory=dict)
    timeout_seconds: float = 120.0
    request_started: float = field(default_factory=time.monotonic)
    editor: Literal["reason", "result"] | None = None

    def back(self) -> bool:
        """Leave an editor without resolving the request or restarting its timeout."""
        if self.editor is None:
            return False
        self.editor = None
        return True

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.request_started >= self.timeout_seconds

    def expire(self) -> ThreadDeferredResponse | None:
        """Deny the current request on Host timeout; never manufacture approval or answers."""
        request = self.request
        message = (
            QUESTION_TIMEOUT_MESSAGE
            if isinstance(request, StructuredQuestionRequestView)
            else INTERACTION_TIMEOUT_MESSAGE
        )
        self.responses.append(
            ApprovalDecision(request_id=request.request_id, approved=False, denial_message=message)
            if isinstance(request, ApprovalRequestView)
            else ExternalToolResult(request_id=request.request_id, denied=True, denial_message=message)
        )
        return self._advance()

    @property
    def request(self) -> DecisionRequestView:
        return self.batch.requests[self.index]

    def selection(self) -> Selection | None:
        request = self.request
        if self.editor is not None:
            return None
        if isinstance(request, StructuredQuestionRequestView):
            question = request.questions[self.question_index]
            return Selection(
                tuple(Choice(option.label, option.label, option.description) for option in question.options),
                multiple=question.multi_select,
            )
        approval = isinstance(request, ApprovalRequestView)
        return Selection(
            (
                Choice("approve", "Approve once", "Execute only this pending request")
                if approval
                else Choice("provide", "Provide result", "Open the JSON result editor"),
                Choice("deny", "Deny", "Do not execute this request" if approval else "Decline to provide a result"),
                Choice("deny with reason", "Deny with reason", "Open the reason editor"),
            )
        )

    def title(self) -> str:
        if self.editor is not None:
            return "Denial reason" if self.editor == "reason" else "Provide JSON result"
        request = self.request
        if isinstance(request, StructuredQuestionRequestView):
            question = request.questions[self.question_index]
            progress = f" · {self.question_index + 1}/{len(request.questions)}" if len(request.questions) > 1 else ""
            return question.header + progress
        return (
            "Choose an action" if isinstance(request, ApprovalRequestView) else f"{request.tool_name} · result required"
        )

    @property
    def prompt_kind(self) -> str:
        return "approval" if isinstance(self.request, ApprovalRequestView) and self.editor is None else "notice"

    def display_prompt(self) -> str:
        """Keep structured display fields separate from untrusted text boundaries."""
        if isinstance(self.request, ApprovalRequestView) and self.editor is None:
            content = _approval_content(self.request, self.index + 1, len(self.batch.requests))
            content["timeout"] = f"{self.timeout_seconds:g}s timeout without approval"
            return json.dumps(content, ensure_ascii=False)
        return self.prompt()

    def prompt(self) -> str:
        request = self.request
        heading = f"Decision {self.index + 1}/{len(self.batch.requests)} · {request.tool_name} · {request.request_id}"
        if self.editor is not None:
            instruction = (
                "Enter a reason to deny this request."
                if self.editor == "reason"
                else "Enter the actual tool result as JSON. Providing a result does not execute the tool."
            )
            return f"{heading}\n{instruction}\nEnter submits · Alt+Enter adds a line · Esc or /cancel returns to choices. The original {self.timeout_seconds:g}s timeout continues."
        if isinstance(request, StructuredQuestionRequestView):
            question = request.questions[self.question_index]
            options = "\n".join(
                f"{index}. {option.label}\n   {option.description}" for index, option in enumerate(question.options, 1)
            )
            review = (
                f"\n[Source preview incomplete; /review {request.request_id} reads retained details]"
                if request.metadata_omitted
                else ""
            )
            return f"{question.header} · {self.question_index + 1}/{len(request.questions)}\n{question.question}\n{options}{review}\nChoose a number or type your own answer. {self.timeout_seconds:g}s timeout · /cancel leaves unanswered."
        if isinstance(request, ApprovalRequestView):
            return (
                _approval_prompt(request, self.index + 1, len(self.batch.requests))
                + f"\n{self.timeout_seconds:g}s timeout without approval."
            )
        arguments = json.dumps(request.arguments, ensure_ascii=False, indent=2)
        metadata = json.dumps(request.metadata, ensure_ascii=False, indent=2) if request.metadata else ""
        content = f"{arguments}\n{metadata}".strip()
        if len(content) > 8192 or request.arguments_omitted or request.metadata_omitted:
            content = content[:8192] + f"\n[Preview incomplete; /review {request.request_id} reads retained details]"
        return f"{heading}\n{content}\n1. Provide result   2. Deny   3. Deny with reason\nDetails: /review {request.request_id} · {self.timeout_seconds:g}s timeout. /cancel keeps the request pending."

    def accept(self, text: str) -> str | ThreadDeferredResponse | None:
        if self.expired:
            return self.expire()
        request = self.request
        value = text.strip()
        if isinstance(request, StructuredQuestionRequestView):
            question = request.questions[self.question_index]
            answer = resolve_choice(
                value, tuple(option.label for option in question.options), multiple=question.multi_select
            )
            self.answers[question.question] = answer
            self.question_index += 1
            if self.question_index < len(request.questions):
                self.request_started = time.monotonic()
                return None
            self.responses.append(
                ExternalToolResult(
                    request_id=request.request_id,
                    result={
                        "answers": {
                            key: list(answer) if isinstance(answer, tuple) else answer
                            for key, answer in self.answers.items()
                        }
                    },
                )
            )
        else:
            approval = isinstance(request, ApprovalRequestView)
            reason = None
            result = None
            if self.editor == "reason":
                if not value:
                    raise ValueError("Enter a denial reason, or press Esc to return to choices.")
                denied, reason = True, value
            elif self.editor == "result":
                denied, result = False, json.loads(value)
            else:
                value = str(resolve_choice(value, ("approve" if approval else "provide", "deny", "deny with reason")))
                if value == "review":
                    return "review"
                if value == "deny with reason" or (not approval and value in {"provide", "provide result"}):
                    self.editor = "reason" if value == "deny with reason" else "result"
                    return None
                verb, _, reason = value.partition(" ")
                allowed = {"approve", "yes", "y", "deny", "no", "n"} if approval else {"deny", "no", "n"}
                if verb not in allowed:
                    raise ValueError("Choose an action by number or name. Use review to inspect details.")
                denied = verb in {"deny", "no", "n"}
            self.responses.append(
                ApprovalDecision(
                    request_id=request.request_id,
                    approved=not denied,
                    denial_message=reason if denied and reason else None,
                )
                if approval
                else ExternalToolResult(
                    request_id=request.request_id,
                    denied=denied,
                    denial_message=reason or None,
                    result=result,
                )
            )
        return self._advance()

    def _advance(self) -> ThreadDeferredResponse | None:
        self.index += 1
        self.question_index = 0
        self.editor = None
        self.answers.clear()
        self.request_started = time.monotonic()
        if self.index == len(self.batch.requests):
            return ThreadDeferredResponse(
                expected_continuation_id=self.batch.continuation_id, responses=tuple(self.responses)
            )
        return None


def _approval_content(request: ApprovalRequestView, index: int, total: int) -> dict[str, str]:
    """Expose review evidence before arguments without interpreting it as markup."""
    incomplete = request.arguments_omitted or request.metadata_omitted

    def preview(value: str, limit: int = 1000) -> str:
        nonlocal incomplete
        lines = value[:limit].splitlines()
        clipped = len(value) > limit or len(lines) > 30
        incomplete |= clipped
        return "\n".join(lines[:30]) + ("\n[Preview truncated]" if clipped else "")

    content = {"tool": request.tool_name, "position": f"{index}/{total}", "details": f"/review {request.request_id}"}
    metadata = dict(request.metadata or {})
    approval = metadata.pop("a13n.harness.tool-approval", None)
    reason = metadata.pop("reason", None) if isinstance(approval, dict) else None
    if isinstance(reason, str) and reason:
        content["reason"] = preview(reason, 2000)
    shared_review = metadata.pop("a13n.harness.tool-review", None)
    review = (
        shared_review if isinstance(approval, dict) and approval.get("tool_id") == "environment.shell_exec" else None
    )
    if isinstance(review, dict):
        risk, reason = review.get("risk"), review.get("reason")
        content["risk"] = preview(risk, 80) if isinstance(risk, str) and risk else "unavailable"
        content["reason"] = preview(reason, 2000) if isinstance(reason, str) and reason else "unavailable"
    elif review is not None:
        content["error"] = "Shell review unavailable (invalid review metadata)"
    arguments = request.arguments
    if isinstance(arguments, str):
        # Native ToolCallPart arguments can be a JSON string or an object.
        # Only expand complete objects; retain malformed/truncated text verbatim.
        try:
            decoded = json.loads(arguments)
        except ValueError:
            pass
        else:
            if isinstance(decoded, dict):
                arguments = decoded
    if isinstance(arguments, dict):
        arguments = dict(arguments)
    command = arguments.get("command") if isinstance(arguments, dict) else None
    if isinstance(arguments, dict) and isinstance(command, str):
        arguments.pop("command")
        cwd = arguments.pop("cwd", None)
        content["command"] = preview(command, 2500)
        content["cwd"] = preview(cwd, 500) if isinstance(cwd, str) else "not specified"
        environment = arguments.pop("environment", None)
        if isinstance(environment, dict) and environment:
            content["environment"] = preview(", ".join(sorted(environment)), 500) + " (values hidden)"
    if arguments is not None and arguments != {}:
        content["arguments"] = preview(json.dumps(arguments, ensure_ascii=False, indent=2))
    if metadata and review is None and approval is None:
        content["context"] = preview(json.dumps(metadata, ensure_ascii=False, indent=2))
    if incomplete:
        content["notice"] = "Preview incomplete; inspect retained request details before deciding."
    return content


def _approval_prompt(request: ApprovalRequestView, index: int, total: int) -> str:
    """Plain-text counterpart for non-rendering callers and transcript output."""
    content = _approval_content(request, index, total)
    parts = [f"Tool Approval Required · {content['position']}", f"Tool: {content['tool']}"]
    for key, label in (
        ("risk", "Risk"),
        ("reason", "Reason"),
        ("error", "Shell review unavailable"),
        ("command", "Command"),
        ("cwd", "Working directory"),
        ("environment", "Environment keys"),
        ("arguments", "Arguments"),
        ("context", "Approval context"),
        ("notice", "Notice"),
    ):
        if key in content:
            parts.append(f"{label}:" + ("\n" if key in {"command", "arguments"} else " ") + content[key])
    parts.append(
        "1. Approve once   2. Deny   3. Deny with reason\nNo automatic approval · Details: " + content["details"]
    )
    return "\n".join(parts)
