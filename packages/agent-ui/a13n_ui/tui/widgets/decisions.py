"""Structured decision collection over detached App projections."""

from __future__ import annotations

from typing import Literal

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Button, Input, Static, TextArea

from a13n_ui.surfaces import DecisionBatchView
from a13n_ui.tui.intents import (
    NavigateDecision,
    OpenReview,
    SubmitDecisionSession,
    UpdateDecisionDraft,
)
from a13n_ui.tui.models import DecisionAnswerDraft, DecisionSessionState
from a13n_ui.tui.widgets.messages import IntentRequested


class DecisionPane(Container):
    """Collect one complete continuation-bound decision batch."""

    def __init__(self) -> None:
        super().__init__(id="decision-pane")
        self._decisions: DecisionBatchView | None = None
        self._session: DecisionSessionState | None = None
        self._structure: tuple[object, ...] | None = None
        self._projecting = False

    def compose(self) -> ComposeResult:
        yield Static(id="decision-progress")
        yield Static(id="decision-stale")
        yield Container(id="decision-body")
        yield Static(id="decision-validation")
        with Horizontal(id="decision-navigation"):
            yield Button("Back", id="decision-back")
            yield Button("Next", id="decision-next")
            yield Button("Submit all", id="decision-submit", variant="primary")

    async def project(
        self,
        decisions: DecisionBatchView,
        session: DecisionSessionState,
        *,
        stale_session: DecisionSessionState | None,
    ) -> None:
        self._decisions = decisions
        self._session = session
        request = decisions.requests[session.request_index]
        structure = (
            decisions.continuation_id,
            tuple(item.request_id for item in decisions.requests),
            session.request_index,
            session.question_index,
            request.kind,
        )
        if structure != self._structure:
            self._structure = structure
            await self._rebuild_body()
        self._update_controls(stale_session=stale_session)

    async def _rebuild_body(self) -> None:
        decisions, session = self._required_state()
        request = decisions.requests[session.request_index]
        draft = session.answer(request.request_id)
        body = self.query_one("#decision-body", Container)
        await body.remove_children()
        widgets: list[Static | Button | Input | TextArea] = []
        if request.kind == "question":
            question = request.questions[session.question_index]
            selected = dict(draft.question_answers).get(question.question, ())
            widgets.extend(
                (
                    Static(question.header, classes="decision-header"),
                    Static(question.question, classes="decision-question"),
                )
            )
            for index, option in enumerate(question.options):
                label = f"{option.label} - {option.description}"
                widgets.append(
                    Button(
                        label,
                        id=f"decision-option-{index}",
                        variant="success" if option.label in selected else "default",
                    )
                )
            labels = {option.label for option in question.options}
            custom = next((value for value in selected if value not in labels), "")
            widgets.append(Input(value=custom, placeholder="Custom answer", id="decision-answer"))
        elif request.kind == "approval":
            widgets.extend(
                (
                    Static(f"Approval required: {request.tool_name}", classes="decision-header"),
                    Static(_bounded_value(request.arguments, request.arguments_omitted)),
                    Button("Review details", id="decision-review"),
                    Button("Approve original", id="decision-approve", variant="success"),
                )
            )
            if request.override_allowed:
                widgets.extend(
                    (
                        TextArea(
                            draft.payload_text,
                            language="json",
                            soft_wrap=True,
                            id="decision-payload",
                        ),
                        Button("Approve override", id="decision-override", variant="warning"),
                    )
                )
            widgets.extend(
                (
                    Input(
                        value=draft.denial_reason,
                        placeholder="Optional denial reason",
                        id="decision-reason",
                    ),
                    Button("Deny", id="decision-deny", variant="error"),
                )
            )
        else:
            widgets.extend(
                (
                    Static(f"External result required: {request.tool_name}", classes="decision-header"),
                    Static(_bounded_value(request.arguments, request.arguments_omitted)),
                    Button("Review details", id="decision-review"),
                    TextArea(
                        draft.payload_text,
                        language="json",
                        soft_wrap=True,
                        id="decision-payload",
                    ),
                    Button("Submit result", id="decision-result", variant="success"),
                    Input(
                        value=draft.denial_reason,
                        placeholder="Denial reason",
                        id="decision-reason",
                    ),
                    Button("Deny", id="decision-deny", variant="error"),
                )
            )
        self._projecting = True
        await body.mount(*widgets)
        self._projecting = False

    def _update_controls(self, *, stale_session: DecisionSessionState | None) -> None:
        decisions, session = self._required_state()
        request = decisions.requests[session.request_index]
        request_count = len(decisions.requests)
        question_count = len(request.questions) if request.kind == "question" else 1
        progress = f"Decision {session.request_index + 1}/{request_count}" + (
            f" - Question {session.question_index + 1}/{question_count}" if request.kind == "question" else ""
        )
        if stale_session is not None:
            progress += " - current request set changed"
        self.query_one("#decision-progress", Static).update(progress)
        stale = self.query_one("#decision-stale", Static)
        stale.display = stale_session is not None
        stale.update("" if stale_session is None else _stale_summary(stale_session))
        self.query_one("#decision-validation", Static).update(session.validation_message or "")
        self.query_one("#decision-back", Button).disabled = session.request_index == 0 and session.question_index == 0
        at_last = session.request_index == request_count - 1 and session.question_index == question_count - 1
        self.query_one("#decision-next", Button).display = not at_last
        self.query_one("#decision-submit", Button).display = at_last

        if request.kind == "question":
            selected = dict(session.answer(request.request_id).question_answers).get(
                request.questions[session.question_index].question,
                (),
            )
            for index, option in enumerate(request.questions[session.question_index].options):
                button = self.query_one(f"#decision-option-{index}", Button)
                button.variant = "success" if option.label in selected else "default"
        else:
            draft = session.answer(request.request_id)
            actions = {
                "decision-approve": "approve",
                "decision-override": "override",
                "decision-deny": "deny",
                "decision-result": "result",
            }
            for button_id, action in actions.items():
                matches = self.query(f"#{button_id}")
                if len(matches):
                    button = matches.first(Button)
                    button.variant = "success" if draft.action == action else "default"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if self._decisions is None or self._session is None:
            return
        button_id = event.button.id or ""
        if button_id.startswith("decision-option-"):
            event.stop()
            self._toggle_option(int(button_id.rsplit("-", 1)[1]))
        elif button_id == "decision-back":
            event.stop()
            self._navigate(-1)
        elif button_id == "decision-next":
            event.stop()
            self._navigate(1)
        elif button_id == "decision-submit":
            event.stop()
            self.post_message(IntentRequested(SubmitDecisionSession(self._session.thread_id)))
        elif button_id == "decision-review":
            event.stop()
            request = self._decisions.requests[self._session.request_index]
            self.post_message(
                IntentRequested(
                    OpenReview(
                        kind="deferred",
                        thread_id=self._session.thread_id,
                        continuation_id=self._session.continuation_id,
                        request_id=request.request_id,
                    )
                )
            )
        elif button_id in {
            "decision-approve",
            "decision-override",
            "decision-deny",
            "decision-result",
        }:
            event.stop()
            if button_id == "decision-approve":
                action: Literal["approve", "override", "deny", "result"] = "approve"
            elif button_id == "decision-override":
                action = "override"
            elif button_id == "decision-deny":
                action = "deny"
            else:
                action = "result"
            self._update_current(action=action)

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._projecting or self._decisions is None or self._session is None:
            return
        if event.input.id == "decision-answer":
            self._update_custom_answer(event.value)
        elif event.input.id == "decision-reason":
            self._update_current(denial_reason=event.value)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if self._projecting or event.text_area.id != "decision-payload":
            return
        self._update_current(payload_text=event.text_area.text)

    def _toggle_option(self, option_index: int) -> None:
        decisions, session = self._required_state()
        request = decisions.requests[session.request_index]
        if request.kind != "question":
            return
        question = request.questions[session.question_index]
        draft = session.answer(request.request_id)
        answers = dict(draft.question_answers)
        selected = list(answers.get(question.question, ()))
        label = question.options[option_index].label
        if question.multi_select:
            if label in selected:
                selected.remove(label)
            else:
                selected.append(label)
        else:
            selected = [] if selected == [label] else [label]
        answers[question.question] = tuple(selected)
        self._emit_draft(draft, question_answers=tuple(answers.items()))

    def _update_custom_answer(self, value: str) -> None:
        decisions, session = self._required_state()
        request = decisions.requests[session.request_index]
        if request.kind != "question":
            return
        question = request.questions[session.question_index]
        draft = session.answer(request.request_id)
        answers = dict(draft.question_answers)
        labels = {option.label for option in question.options}
        selected = [item for item in answers.get(question.question, ()) if item in labels]
        if value.strip():
            selected = [value.strip()]
        answers[question.question] = tuple(selected)
        self._emit_draft(draft, question_answers=tuple(answers.items()))

    def _update_current(
        self,
        *,
        action: Literal["approve", "override", "deny", "result"] | None = None,
        payload_text: str | None = None,
        denial_reason: str | None = None,
    ) -> None:
        decisions, session = self._required_state()
        request = decisions.requests[session.request_index]
        draft = session.answer(request.request_id)
        self._emit_draft(
            draft,
            action=draft.action if action is None else action,
            payload_text=draft.payload_text if payload_text is None else payload_text,
            denial_reason=draft.denial_reason if denial_reason is None else denial_reason,
        )

    def _emit_draft(
        self,
        draft: DecisionAnswerDraft,
        *,
        question_answers: tuple[tuple[str, tuple[str, ...]], ...] | None = None,
        action: Literal["approve", "override", "deny", "result"] | None = None,
        payload_text: str | None = None,
        denial_reason: str | None = None,
    ) -> None:
        updated = DecisionAnswerDraft(
            request_id=draft.request_id,
            question_answers=draft.question_answers if question_answers is None else question_answers,
            response_text=draft.response_text,
            action=draft.action if action is None else action,
            payload_text=draft.payload_text if payload_text is None else payload_text,
            denial_reason=draft.denial_reason if denial_reason is None else denial_reason,
        )
        session = self._session
        assert session is not None
        self.post_message(IntentRequested(UpdateDecisionDraft(session.thread_id, updated)))

    def _navigate(self, direction: int) -> None:
        decisions, session = self._required_state()
        request_index = session.request_index
        question_index = session.question_index
        request = decisions.requests[request_index]
        if direction > 0:
            if request.kind == "question" and question_index + 1 < len(request.questions):
                question_index += 1
            elif request_index + 1 < len(decisions.requests):
                request_index += 1
                question_index = 0
        elif question_index > 0:
            question_index -= 1
        elif request_index > 0:
            request_index -= 1
            previous = decisions.requests[request_index]
            question_index = len(previous.questions) - 1 if previous.kind == "question" else 0
        self.post_message(
            IntentRequested(
                NavigateDecision(
                    thread_id=session.thread_id,
                    request_index=request_index,
                    question_index=question_index,
                )
            )
        )

    def _required_state(self) -> tuple[DecisionBatchView, DecisionSessionState]:
        assert self._decisions is not None
        assert self._session is not None
        return self._decisions, self._session


def _stale_summary(session: DecisionSessionState) -> str:
    parts = ["Previous answers were not submitted because App truth changed:"]
    for draft in session.answers:
        values = [value for _, answers in draft.question_answers for value in answers]
        summary = draft.action or ", ".join(values) or "unanswered"
        parts.append(f"{draft.request_id}: {summary}")
    return "\n".join(parts)[:8192]


def _bounded_value(value: object, omitted: bool) -> str:
    if omitted:
        return "Arguments were omitted by the App surface bound."
    if value is None:
        return "No arguments supplied."
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)[: 64 * 1024]


__all__ = ["DecisionPane"]
