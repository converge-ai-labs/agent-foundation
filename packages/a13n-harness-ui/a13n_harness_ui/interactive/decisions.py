"""Presentation-only drafts for one exact App-owned continuation batch."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

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

QUESTION_TIMEOUT_MESSAGE = (
    "The user did not respond before this clarification timed out. "
    "Do not wait for or repeat the same question. Continue with reasonable assumptions where possible. "
    "No answer or approval was provided."
)


@dataclass(slots=True)
class DecisionInteraction:
    batch: DecisionBatchView
    index: int = 0
    question_index: int = 0
    responses: list[ApprovalDecision | ExternalToolResult] = field(default_factory=list)
    answers: dict[str, str | tuple[str, ...]] = field(default_factory=dict)
    timeout_seconds: float = 120.0
    question_started: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return (
            isinstance(self.request, StructuredQuestionRequestView)
            and time.monotonic() - self.question_started >= self.timeout_seconds
        )

    def expire_question(self) -> ThreadDeferredResponse | None:
        """Reject this entire question call; never invent partial answers or approve another request."""
        if not isinstance(self.request, StructuredQuestionRequestView):
            raise ValueError("Only structured questions have an automatic waiting timeout")
        self.responses.append(
            ExternalToolResult(request_id=self.request.request_id, denied=True, denial_message=QUESTION_TIMEOUT_MESSAGE)
        )
        return self._advance()

    @property
    def request(self) -> DecisionRequestView:
        return self.batch.requests[self.index]

    def selection(self) -> Selection | None:
        request = self.request
        if isinstance(request, StructuredQuestionRequestView):
            question = request.questions[self.question_index]
            return Selection(
                tuple(Choice(option.label, option.label, option.description) for option in question.options),
                multiple=question.multi_select,
            )
        if isinstance(request, ApprovalRequestView):
            return Selection(
                (
                    Choice("review", "Inspect request details", "Read retained arguments and review evidence"),
                    Choice("approve", "Approve once", "Execute only this pending request"),
                    Choice("deny", "Deny", "Do not execute this request"),
                )
            )
        return None

    def title(self) -> str:
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
        return "approval" if isinstance(self.request, ApprovalRequestView) else "notice"

    def display_prompt(self) -> str:
        """Keep structured display fields separate from untrusted text boundaries."""
        if isinstance(self.request, ApprovalRequestView):
            return json.dumps(
                _approval_content(self.request, self.index + 1, len(self.batch.requests)), ensure_ascii=False
            )
        return self.prompt()

    def prompt(self) -> str:
        request = self.request
        heading = f"Decision {self.index + 1}/{len(self.batch.requests)} · {request.tool_name} · {request.request_id}"
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
            return _approval_prompt(request, self.index + 1, len(self.batch.requests))
        arguments = json.dumps(request.arguments, ensure_ascii=False, indent=2)
        metadata = json.dumps(request.metadata, ensure_ascii=False, indent=2) if request.metadata else ""
        content = f"{arguments}\n{metadata}".strip()
        if len(content) > 8192 or request.arguments_omitted or request.metadata_omitted:
            content = content[:8192] + f"\n[Preview incomplete; /review {request.request_id} reads retained details]"
        return f"{heading}\n{content}\nEnter a JSON result, or type deny [reason]. /cancel keeps the request pending."

    def accept(self, text: str) -> str | ThreadDeferredResponse | None:
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
                self.question_started = time.monotonic()
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
        elif isinstance(request, ApprovalRequestView):
            value = str(resolve_choice(value, ("review", "approve", "deny")))
            if value == "review":
                return "review"
            verb, _, reason = value.partition(" ")
            if verb not in {"approve", "yes", "y", "deny", "no", "n"}:
                raise ValueError("Choose approve/yes, deny/no [reason], or review. Ordinary text never approves.")
            self.responses.append(
                ApprovalDecision(
                    request_id=request.request_id,
                    approved=verb in {"approve", "yes", "y"},
                    denial_message=reason if reason and verb in {"deny", "no", "n"} else None,
                )
            )
        else:
            denied = value == "deny" or value.startswith("deny ")
            self.responses.append(
                ExternalToolResult(
                    request_id=request.request_id,
                    denied=denied,
                    denial_message=(value[5:].strip() or "Denied in CLI") if denied else None,
                    result=None if denied else json.loads(value),
                )
            )
        return self._advance()

    def _advance(self) -> ThreadDeferredResponse | None:
        self.index += 1
        self.question_index = 0
        self.answers.clear()
        self.question_started = time.monotonic()
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
    review = metadata.pop("a13n.harness.shell-review", None)
    if isinstance(review, dict):
        if review.get("status") == "error":
            content["error"] = "The reviewer failed; no risk assessment or reason is available."
        else:
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
        "1. Inspect request details   2. Approve once   3. Deny\nNo automatic approval · " + content["details"]
    )
    return "\n".join(parts)
