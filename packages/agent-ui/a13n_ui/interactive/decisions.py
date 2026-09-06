"""Presentation-only drafts for one exact App-owned continuation batch."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from a13n_ui.surfaces import (
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
                    Choice("review", "Inspect full request", "Read the command and review evidence before deciding"),
                    Choice("approve", "Approve once", "Execute only this pending request"),
                    Choice("deny", "Deny", "Do not execute this request"),
                )
            )
        return None

    def prompt(self) -> str:
        request = self.request
        heading = f"Decision {self.index + 1}/{len(self.batch.requests)} · {request.tool_name} · {request.request_id}"
        if isinstance(request, StructuredQuestionRequestView):
            question = request.questions[self.question_index]
            return f"{heading}\n{question.header} ({self.question_index + 1}/{len(request.questions)})\n{question.question}\nChoose a number or type your own answer. /cancel keeps the request pending."
        arguments = json.dumps(request.arguments, ensure_ascii=False, indent=2)
        metadata = json.dumps(request.metadata, ensure_ascii=False, indent=2) if request.metadata else ""
        content = f"{arguments}\n{metadata}".strip()
        if len(content) > 8192 or request.arguments_omitted or request.metadata_omitted:
            content = content[:8192] + f"\n[Preview incomplete; /review {request.request_id} reads retained details]"
        if isinstance(request, ApprovalRequestView):
            return f"{heading}\n{content}\nNo automatic approval. Choose an action, or type yes / no [reason] / review."
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
        self.index += 1
        self.question_index = 0
        self.answers.clear()
        if self.index == len(self.batch.requests):
            return ThreadDeferredResponse(
                expected_continuation_id=self.batch.continuation_id, responses=tuple(self.responses)
            )
        return None
