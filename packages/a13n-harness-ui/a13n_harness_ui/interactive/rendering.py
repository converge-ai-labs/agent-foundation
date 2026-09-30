"""Semantic stream presentation with bounded per-message display state."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from a13n_stream_protocol import ContentMetadata
from a13n_stream_protocol.display import DisplayBlock, DisplayState

from .input_display import composer_piece
from .panels import capability_panel, shell_outcome, shell_result_preview, tool_arguments, tool_preview, tool_result
from .tool_rows import (
    CONTEXT_TOOLS,
    EXPLORATION_TOOLS,
    ExplorationGroup,
    ExplorationMember,
    failure_reason,
    semantic_tool_row,
    subagent_result_row,
)
from .transcript import Transcript

if TYPE_CHECKING:
    from a13n_harness.usage import BoundedRequestUsage, ModelUsageRecord

    from a13n_harness_ui.goal import GoalView
    from a13n_harness_ui.storage.usage import UsageTotals
    from a13n_harness_ui.surfaces import NotePage, StructuredQuestionRequestView

    from .local_shell import LocalShellEvent


def terminal_text(value: str) -> str:
    """Render untrusted content as text, never as terminal control sequences."""
    return "".join(char for char in value if char in "\n\t" or (ord(char) >= 32 and not 127 <= ord(char) <= 159))


def _elapsed_text(elapsed: float) -> str:
    hours, remainder = divmod(max(0, round(elapsed)), 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


@dataclass(slots=True)
class Status:
    theme: Literal["auto", "dark", "light"] = "auto"
    theme_explicit: bool = False
    state: str = "starting"
    goal: GoalView | None = None
    agent: str = "not configured"
    model: str = "not configured"
    thinking: str = "default"
    service_tier: str | None = None
    fast: str = "default"
    reasoning_mode: str = "default"
    reasoning_mode_description: str = "Provider default"
    environment: str = "not selected"
    directory: Path | None = None
    context_window: int | None = None
    context_tokens: int | None = None
    mode: str = "concise"
    mode_explicit: bool = False
    show_status: bool = True
    max_tool_result_lines: int = 5
    max_tool_argument_chars: int = 8192
    started: float | None = None
    elapsed: float = 0
    session_id: str | None = None
    usage: BoundedRequestUsage | None = None
    requests: int = 0
    unknown_costs: int = 0
    notices: list[str] = field(default_factory=list)
    _usage_ids: set[str] = field(default_factory=set)

    def reset_usage(self) -> None:
        self.usage = None
        self.requests = 0
        self.unknown_costs = 0
        self._usage_ids.clear()

    def restore_usage(self, totals: UsageTotals) -> None:
        """Replace the Thread baseline; live IDs belong only to the next operation."""
        from a13n_harness.usage import BoundedRequestUsage

        self.reset_usage()
        self.requests = totals.model_requests
        self.unknown_costs = totals.unknown_model_costs
        if self.requests:
            self.usage = BoundedRequestUsage.model_validate({**dict(totals.tokens), "cost": totals.model_cost_usd})

    def record_usage(self, record: ModelUsageRecord) -> None:
        from a13n_harness.usage import BoundedRequestUsage

        if record.record_id in self._usage_ids:
            return
        self._usage_ids.add(record.record_id)
        current = record.request_usage
        previous = self.usage
        self.requests += 1
        counters = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
        values = current.model_dump(include=set(counters))
        if previous is not None:
            old = previous.model_dump(include=set(counters))
            values = {key: values[key] + old[key] for key in counters}
        self.unknown_costs += current.cost is None
        cost = (current.cost or Decimal(0)) + (previous.cost or Decimal(0) if previous is not None else Decimal(0))
        self.usage = BoundedRequestUsage(**values, cost=cost)

    @property
    def cache_rate(self) -> float | None:
        if self.usage is None:
            return None
        total = self.usage.input_tokens + self.usage.output_tokens
        return 100 * self.usage.cache_read_tokens / total if total else None

    def usage_details(self) -> str:
        if self.usage is None:
            return "Conversation usage: unavailable (no observed model response)."
        usage = self.usage
        cost = (
            "unknown"
            if self.requests == self.unknown_costs or usage.cost is None
            else f"USD {usage.cost:.6f}{'+' if self.unknown_costs else ''} (model estimate, not subscription billing)"
        )
        return (
            f"Observed conversation: {self.requests} requests · {self.total_tokens:,} total tokens · input {usage.input_tokens:,} · output {usage.output_tokens:,}\n"
            f"Cache read {usage.cache_read_tokens:,} · cache write {usage.cache_write_tokens:,} (provider-reported counters)\n"
            f"Cache rate: {self.cache_rate_text} of input + output. "
            f"Cost: {cost}. {self.unknown_costs} unknown-cost responses. Root, subagents and auxiliary models included; non-model usage separate."
        )

    @property
    def cache_rate_text(self) -> str:
        return "--" if self.cache_rate is None else f"{self.cache_rate:.1f}%"

    @property
    def total_tokens(self) -> int | None:
        return None if self.usage is None else self.usage.input_tokens + self.usage.output_tokens

    @property
    def service_tier_text(self) -> str:
        return "Fast (priority)" if self.service_tier == "priority" else self.service_tier or "provider default"

    def line(self, width: int | None = None) -> str:
        elapsed = time.monotonic() - self.started if self.started is not None else self.elapsed
        from prompt_toolkit.utils import get_cwidth

        context = "--" if self.context_tokens is None else f"{self.context_tokens:,}"
        if self.context_tokens is not None and self.context_window:
            context += f" ({100 * self.context_tokens / self.context_window:.0f}%)"
        cost = (
            "cost --"
            if self.usage is None or self.usage.cost is None or self.requests == self.unknown_costs
            else f"${self.usage.cost:.4f}{'+' if self.unknown_costs else ''}"
        )
        compact = width is not None and width < 60
        total = self.total_tokens
        token_count = "--"
        if total is not None:
            token_count = (
                f"{total / 1_000_000:.1f}M"
                if total >= 1_000_000
                else f"{total / 1_000:.1f}K"
                if total >= 1000
                else str(total)
            )
        state = self.state.capitalize()
        speed_label = "Ultrafast" if self.fast == "ultrafast" else "Fast" if self.fast == "on" else None
        if compact and speed_label is not None:
            state += f" {speed_label}"
        if compact and self.reasoning_mode == "pro":
            state += " Pro"
        goal = self.goal
        goal_label = None
        if goal is not None:
            phase = (
                "restore audit pending" if goal.needs_restore_audit and goal.active else goal.status.replace("_", " ")
            )
            goal_label = f"Goal {phase} {goal.iteration}/{goal.max_iterations}"
        fields = [
            *((goal_label,) if goal_label is not None else ()),
            state,
            *((speed_label,) if not compact and speed_label is not None else ()),
            *(("Pro",) if not compact and self.reasoning_mode == "pro" else ()),
            f"{'tok' if compact else 'tokens'} {token_count}",
            f"ctx {context}",
            f"cache {self.cache_rate_text}",
            cost,
            f"{'think' if compact else 'Thinking'} {self.thinking}",
            self.model.split(":")[-1],
            _elapsed_text(elapsed),
        ]
        while width is not None and get_cwidth(" · ".join(fields)) + 2 > width and len(fields) > 1:
            fields.pop()
        text = terminal_text(" " + " · ".join(fields) + " ")
        if width is not None:
            while get_cwidth(text) > width and text:
                text = text[:-1]
        return text


@dataclass(slots=True)
class _ToolPreview:
    name: str
    block_id: int | None = None
    semantic: str = ""
    read_path: str | None = None
    group: ExplorationGroup | None = None
    member: ExplorationMember | None = None


@dataclass(slots=True)
class _QuestionResult:
    block_id: int | None = None
    signature: tuple[str, str] | None = None


@dataclass(slots=True)
class _QuestionReceipt:
    request: StructuredQuestionRequestView
    arguments: str
    block_id: int | None = None
    attempts: dict[str, _QuestionResult] = field(default_factory=dict)


@dataclass(slots=True)
class _ShellObservation:
    command: str = ""
    completion_seen: bool = False
    phase: str = "unknown"
    exit_code: int | None = None
    background: bool = False


class StreamRenderer:
    """Render reduced display values; never interpret native or AG-UI event sequences.

    The producer owns semantic state. This adapter owns bounded terminal rows,
    presentation identities, and a bounded drain buffer for observation/testing.
    """

    def __init__(self, status: Status, *, limit: int | None = None) -> None:
        from .tasks import TaskPanel

        self.status = status
        self.transcript = Transcript()
        self.tasks = TaskPanel()
        self._notes: NotePage | None = None
        self._local_inputs: dict[str, int] = {}
        self._display_rows: dict[str, int] = {}
        self._limit = limit
        self._pending: list[str] = []
        self._size = 0
        self._tools: dict[tuple[str, str], _ToolPreview] = {}
        self._shell_processes: dict[tuple[str, str], _ShellObservation] = {}
        self._shell_observations_omitted = False
        self.assistant_seen = False
        self.gap = False
        self.boundary = False
        self._line_open = False
        self._local_output: dict[str, int] = {}
        self._exploration: ExplorationGroup | None = None
        # Root deferred call IDs survive a fresh response Run. Other tools and
        # child executions keep their ordinary Run-local correlation.
        self._questions: dict[str, _QuestionReceipt] = {}

    def register_questions(self, request: StructuredQuestionRequestView) -> None:
        """Retain a typed root request, not a local answer or acceptance receipt.

        The shell calls this when activating a pending question (including one
        restored without live tool events). Registration never emits Answered.
        """
        if request.tool_name != "ask_user_question" or request.request_id in self._questions:
            return
        arguments = json.dumps(
            {"questions": [question.model_dump(mode="json") for question in request.questions]}, ensure_ascii=False
        )
        self._questions[request.request_id] = _QuestionReceipt(request, arguments)
        self._trim_questions()

    def _trim_questions(self) -> None:
        while (
            len(self._questions) > 128
            or sum(len(item.arguments.encode("utf-8")) for item in self._questions.values()) > self.transcript.max_bytes
        ):
            self._questions.pop(next(iter(self._questions)))

    def _register_question_arguments(self, call_id: str, preview: _ToolPreview, arguments: str) -> None:
        """Recognize replayed calls only by the public tool name and request schema."""
        from a13n_harness.capabilities import AskUserQuestionRequest
        from pydantic import ValidationError

        from a13n_harness_ui.surfaces import QuestionView, StructuredQuestionRequestView

        try:
            request = AskUserQuestionRequest.model_validate_json(arguments)
            view = StructuredQuestionRequestView(
                request_id=call_id,
                tool_name=preview.name,
                questions=tuple(QuestionView.model_validate(question.model_dump()) for question in request.questions),
            )
        except ValidationError:
            return
        self.register_questions(view)
        receipt = self._questions.get(call_id)
        if receipt is not None:
            receipt.block_id = receipt.block_id or preview.block_id
            receipt.arguments = arguments
            self._trim_questions()

    def _question_result(self, receipt: _QuestionReceipt, text: str, state: str, run_id: str) -> None:
        """Render the producer's result, once per response attempt and value."""
        from a13n_harness.toolsets.interaction import validate_user_question_result

        attempt = receipt.attempts.get(run_id)
        if attempt is None:
            attempt = _QuestionResult(receipt.block_id if not receipt.attempts else None)
            receipt.attempts[run_id] = attempt
            while len(receipt.attempts) > 16:
                receipt.attempts.pop(next(iter(receipt.attempts)))
        try:
            content = json.loads(text)
        except ValueError:
            content = text
        signature = (sha256(json.dumps(content, sort_keys=True).encode()).hexdigest(), state)
        if attempt.signature == signature:
            return
        attempt.signature = signature
        answers: dict[str, object] | None = None
        if state == "returned":
            try:
                answers = validate_user_question_result(json.loads(receipt.arguments), content)
            except ValueError:
                state = "unavailable"
        lines = []
        for question in receipt.request.questions:
            if lines:
                lines.append("")
            answer: object = None
            answer_text = "Result unavailable; Ctrl+O details"
            if answers is not None:
                values = answers["answers"]
                answer = values.get(question.question, answers.get("response")) if isinstance(values, dict) else None
                answer_text = ", ".join(str(item) for item in answer) if isinstance(answer, list) else str(answer)
            elif state != "unavailable":
                error = content.get("error") if isinstance(content, dict) else None
                answer_text = (
                    content
                    if isinstance(content, str)
                    else error
                    if isinstance(error, str)
                    else failure_reason(text, state)
                )
            title = "Answered" if answers is not None else "Not answered"
            lines.extend((f"{title} · {question.header}", question.question))
            selected = answer if isinstance(answer, list) else [answer]
            for option in question.options:
                marker = "[x]" if option.label in selected else "[ ]"
                lines.append(f"  {marker} {option.label}")
            # A free-text answer is not necessarily one of the offered labels.
            if answers is None or any(value not in {option.label for option in question.options} for value in selected):
                lines.append(f"→ {answer_text}")
        brief = terminal_text("\n".join(lines))
        detail = terminal_text(f"Tool result | {state}\n{text}\n")
        body = (
            terminal_text(
                f"ask_user_question | {state}\nArguments | {receipt.request.request_id}\n{receipt.arguments}\n"
            )
            + detail
        )
        block_id = attempt.block_id
        if block_id is None or not self.transcript.replace(block_id, body, kind="question_receipt"):
            block_id = self.transcript.append(body, kind="question_receipt")
        attempt.block_id = block_id
        self.transcript.preview(block_id, brief, 64, limit=self.transcript.block_bytes)
        self.transcript.blocks[block_id].concise_hidden = False
        self.finish()
        self.append(brief + "\n", display=False)

    def _shell_observation(self, run_id: str, process_id: str) -> _ShellObservation:
        key = (run_id, process_id)
        observation = self._shell_processes.setdefault(key, _ShellObservation())
        while len(self._shell_processes) > 128:
            self._shell_processes.pop(next(iter(self._shell_processes)))
            self._shell_observations_omitted = True
        return observation

    def _shell_command(self, name: str, arguments: str, run_id: str) -> str:
        """Resolve only explicit launch arguments or a same-Run retained handle."""
        try:
            value = json.loads(arguments)
        except ValueError:
            return ""
        if not isinstance(value, dict):
            return ""
        if name in {"shell_exec", "shell_start"}:
            command = value.get("command")
            return " ".join(command.split())[:500] if isinstance(command, str) else ""
        process_id = value.get("process_id")
        if name == "shell_wait" and isinstance(process_id, str):
            observation = self._shell_processes.get((run_id, process_id))
            return observation.command if observation else ""
        return ""

    def _observe_shell_result(self, text: str, command: str, run_id: str) -> None:
        try:
            result = json.loads(text)
        except ValueError:
            return
        if not isinstance(result, dict) or not isinstance(result.get("process_id"), str):
            return
        observation = self._shell_observation(run_id, result["process_id"])
        if command:
            observation.command = " ".join(command.split())[:500]
        status = result.get("status")
        phase = status.get("phase") if isinstance(status, dict) else None
        if isinstance(phase, str):
            if phase == "running":
                # A returned running handle is background work, unlike an
                # in-flight foreground invocation's routine status event.
                observation.background = True
            if phase == "running" and observation.phase in {"exited", "signaled", "timed_out", "cancelled", "failed"}:
                # A completion event can overtake delivery of the tool's earlier
                # running snapshot. Do not resurrect the completed process.
                return
            observation.phase = phase
            if isinstance(status, dict) and type(status.get("exit_code")) is int:
                observation.exit_code = status["exit_code"]
            if phase in {"exited", "signaled", "timed_out", "cancelled", "failed"}:
                observation.completion_seen = True

    def clear_process_observations(self) -> None:
        self._shell_processes.clear()
        self._shell_observations_omitted = False

    def end_process_observations(self, run_id: str | None = None) -> None:
        for (observed_run, _), observation in self._shell_processes.items():
            if (run_id is None or observed_run == run_id) and observation.phase == "running":
                observation.phase = "unavailable"

    @property
    def background_hint(self) -> str:
        running = sum(item.background and item.phase == "running" for item in self._shell_processes.values())
        uncertain = self.gap or self._shell_observations_omitted
        if not running and not uncertain:
            return ""
        return f"Background {running}{'+' if uncertain else ''} observed · /ps"

    def process_details(self) -> str:
        lines = ["Background processes · last observed"]
        processes = [(key, value) for key, value in self._shell_processes.items() if value.background]
        for (run_id, process_id), item in processes[-16:]:
            phase = f"failed (exit {item.exit_code})" if item.phase == "exited" and item.exit_code else item.phase
            lines.append(f"{process_id} · {phase} · {item.command or 'command unavailable'} · Run {run_id}")
        if not processes:
            lines.append("No background processes observed.")
        if len(processes) > 16 or self._shell_observations_omitted:
            lines.append("Older observations omitted · showing up to 16.")
        if self.gap:
            lines.append("Live output incomplete · status may be stale.")
        if processes:
            lines.append("Ctrl+O · tool output")
        return terminal_text("\n".join(lines))

    def _shell_notification(self, event: Mapping[str, object], run_id: str, child_label: str = "") -> None:
        process_id = event.get("process_id")
        if event.get("callback") is not True or not isinstance(process_id, str):
            return
        phase = event.get("phase")
        if not isinstance(phase, str) or phase not in {"exited", "signaled", "timed_out", "cancelled", "failed"}:
            return
        observation = self._shell_observation(run_id, process_id)
        if observation.completion_seen:
            return
        observation.completion_seen = True
        outcome = shell_outcome(event) or "finished"
        label = observation.command or "background command"
        brief = f"{label} · {outcome}" + (f" · {child_label}" if child_label else "")
        self.finish()
        # Keep diagnostic identity and status available on expansion, not as a
        # second process lifecycle panel in the default conversation view.
        body = brief + "\n" + json.dumps(dict(event), ensure_ascii=False, indent=2)
        block = self.transcript.append(terminal_text(body), collapsed_lines=1, kind="tool")
        self.transcript.preview(block, terminal_text(brief), 1)
        self.append(brief + "\n", display=False)

    @property
    def note_count(self) -> int | None:
        return None if self._notes is None else self._notes.total

    def restore_notes(self, page: NotePage, *, force: bool = False) -> None:
        previous = self._notes
        self._notes = page
        if not force and (
            (previous is None and not page.total)
            or (previous is not None and previous.notes == page.notes and previous.omitted == page.omitted)
        ):
            return
        lines = [f"Notes · {page.total} saved"]
        for note in page.notes:
            lines.extend((f"{note.key}", note.value, ""))
        if page.omitted:
            lines.append(
                f"[{page.omitted} notes omitted: 256-note / 256 KiB display budget. Saved values are unchanged.]"
            )
        source = "\n".join(lines).rstrip() + "\n"
        block = self.transcript.append(terminal_text(source), kind="notes")
        if not force:
            brief = lines[0]
            if page.omitted:
                brief += f" · {page.omitted} omitted"
            brief += " · Ctrl+O details"
            if page.notes:
                brief += " · " + ", ".join(" ".join(note.key.split()) for note in page.notes)
            self.transcript.preview(block, terminal_text(brief[:960]))
        self.append(source, display=False)

    def local_input(self, source_id: str, text: str) -> None:
        self.finish()
        self._local_inputs[source_id] = self.transcript.append("> " + terminal_text(text), kind="user")
        while len(self._local_inputs) > 128:
            self._local_inputs.pop(next(iter(self._local_inputs)))

    @property
    def limit(self) -> int:
        return self._limit or self.status.max_tool_argument_chars

    def append(
        self,
        text: str,
        *,
        display: bool = True,
        markdown: bool = False,
        collapsed_lines: int | None = None,
        collapsed_chars: int | None = None,
        kind: str = "text",
    ) -> None:
        safe = terminal_text(text)
        if display and safe:
            self.transcript.append(
                safe, markdown=markdown, collapsed_lines=collapsed_lines, collapsed_chars=collapsed_chars, kind=kind
            )
        self._pending.append(safe)
        self._size += len(safe)
        if self._size > 256 * 1024:
            self._pending = ["".join(self._pending)[-256 * 1024 :]]
            self._size = len(self._pending[0])
        if safe:
            self._line_open = not safe.endswith("\n")

    @property
    def should_flush(self) -> bool:
        return self.boundary or self._size >= self.limit

    def drain(self) -> str:
        result = "".join(self._pending)
        self._pending.clear()
        self._size = 0
        self.boundary = False
        return result

    def finish(self) -> None:
        if self._line_open:
            self.append("\n", display=False)
        self.boundary = True

    def local_shell(self, event: LocalShellEvent) -> None:
        """Render host-operation events without fabricating an agent Run or message."""
        if event.kind == "started":
            self.finish()
            self._local_output.clear()
            self.append(f"[Local shell · running]\n$ {event.command}\n", kind="shell")
        elif event.kind == "output":
            stream = event.stream or "stdout"
            text = terminal_text(event.text)
            block = self._local_output.get(stream)
            if block is None or not self.transcript.extend(block, text):
                self._local_output[stream] = self.transcript.append(
                    f"{stream}\n{text}",
                    kind="shell",
                    streaming=True,
                )
            self.append(text, display=False)
        else:
            for block in self._local_output.values():
                self.transcript.complete(block)
            self._local_output.clear()
            self.finish()
            code = f" · exit {event.exit_code}" if event.exit_code is not None else ""
            self.append(f"[Local shell · {event.phase}{code} · {event.elapsed:.1f}s]\n", kind="shell")
            if event.truncated:
                self.append("[Output display limit reached; remaining output was drained and discarded.]\n")
            self.boundary = True

    def _row(
        self, key: str, source: str, *, kind: str = "text", markdown: bool = False, streaming: bool = False
    ) -> int:
        """Replace a presentation row with a complete producer value."""
        source = terminal_text(source)
        row = self._display_rows.get(key)
        previous = self.transcript.blocks.get(row) if row is not None else None
        previous_source = previous.source if previous is not None else ""
        if row is None or not self.transcript.replace(row, source, kind=kind):
            row = self.transcript.append(source, kind=kind, markdown=markdown, streaming=streaming)
            self._display_rows[key] = row
        block = self.transcript.blocks[row]
        block.markdown = markdown
        block.streaming = streaming
        while len(self._display_rows) > 1024:
            self._display_rows.pop(next(iter(self._display_rows)))
        if source != previous_source:
            self.append(source[len(previous_source) :] if source.startswith(previous_source) else source, display=False)
        self.boundary = True
        return row

    def remove_blocks(self, state: DisplayState, identifiers: tuple[str, ...]) -> tuple[str, ...]:
        """Discard affected rows and return surviving members of joined rows."""
        rows = {self._display_rows[key] for key in identifiers if key in self._display_rows}
        survivors: list[str] = []
        for key, row in tuple(self._display_rows.items()):
            if row not in rows:
                continue
            self._display_rows.pop(key)
            self.transcript.remove(row)
            detail = self._display_rows.pop(key + ":details", None)
            if detail is not None:
                self.transcript.remove(detail)
            if key in state.blocks:
                survivors.append(key)
        self._exploration = None
        return tuple(survivors)

    def display_blocks(
        self, state: DisplayState, identifiers: tuple[str, ...], *, child: bool = False, execution_id: str | None = None
    ) -> None:
        """Read only changed, already-applied blocks, including inline child scopes."""
        for identifier in identifiers:
            block = state.blocks.get(identifier)
            if block is None:
                continue
            scope = state.scopes[block.scope_id]
            nested = child or scope.parent_scope_id is not None
            identity = execution_id or scope.invocation_id or scope.run_id
            if nested and self.status.mode != "detailed":
                if block.kind == "tool_chunk":
                    self._tool_block(block, state.position.producer.run_id, scope.run_id, identity, visible=False)
                continue
            content = block.content
            metadata = ContentMetadata.from_native(content.get("metadata"))
            if not metadata.display:
                continue
            if block.kind in {"input", "media"} and not nested and metadata.source_id in self._local_inputs:
                continue
            running = block.status in {"pending", "running"}
            if block.kind in {"input", "media"} and composer_piece(metadata) is not None:
                pieces: dict[int, str] = {}
                members: list[str] = []
                for candidate in state.blocks.values():
                    candidate_metadata = ContentMetadata.from_native(candidate.content.get("metadata"))
                    piece = composer_piece(candidate_metadata)
                    if (
                        candidate.scope_id != block.scope_id
                        or candidate.kind not in {"input", "media"}
                        or not candidate_metadata.display
                        or candidate_metadata.source_id != metadata.source_id
                        or piece is None
                    ):
                        continue
                    members.append(candidate.id)
                    index, label = piece
                    value = candidate.content.get("text", "")
                    pieces[index] = f"[{label}]" if label else value if isinstance(value, str) else ""
                prefix = f"> Subagent {identity} · " if nested else "> "
                row = self._row(
                    f"{block.scope_id}:composer:{metadata.source_id}",
                    prefix + "".join(pieces[index] for index in sorted(pieces)),
                    kind="user",
                )
                for member in members:
                    self._display_rows[member] = row
                continue
            if block.kind in {"input", "text", "reasoning", "media"}:
                self._exploration = None
                text = content.get("text", "")
                text = text if isinstance(text, str) else ""
                user = block.kind == "input" or (block.kind == "media" and content.get("message_kind") == "request")
                if block.kind == "media":
                    media = content.get("media")
                    if isinstance(media, dict):
                        label = str(media.get("media_type") or media.get("kind") or "media")
                        size = media.get("size_bytes")
                        if isinstance(size, int):
                            label += f" · {size:,} bytes"
                        reference = media.get("url") or media.get("file_id")
                        if isinstance(reference, str):
                            label += f" · {reference}"
                        text += f"[{label}]"
                notification = user and (metadata.model_extra or {}).get("a13n.steering-source") in {
                    "background_process",
                    "async_subagent",
                }
                prefix = (
                    "Activity · "
                    if notification
                    else (f"**Subagent · {identity}**\n\n" if nested else "") + ("> " if user else "")
                )
                self._row(
                    block.id,
                    prefix + text,
                    kind="tool"
                    if notification
                    else "user"
                    if user
                    else "thinking"
                    if block.kind == "reasoning"
                    else "text",
                    markdown=not user,
                    streaming=running,
                )
                if block.kind == "text" and not nested and text:
                    self.assistant_seen = True
                if not user and not nested and self.status.state != "cancelling":
                    self.status.state = "thinking" if block.kind == "reasoning" else "responding"
                continue
            if block.kind == "tool_chunk":
                self._tool_block(block, state.position.producer.run_id, scope.run_id, identity if nested else "")
                continue
            if block.kind == "context_summary" or (
                block.kind == "extension" and content.get("name") == "a13n.display.context_operation"
            ):
                self._context_block(state, block)
                continue
            if block.kind == "extension":
                value = content.get("value")
                if not isinstance(value, dict):
                    continue
                name = content.get("name")
                if name == "a13n.display.task" and not nested:
                    self.tasks.ingest(
                        {
                            "type": "task_changed",
                            "task": value,
                            "task_state_version": content.get("task_state_version", 0),
                        }
                    )
                    continue
                if content.get("event_kind") == "tool":
                    if (
                        value.get("type") == "tool_extra"
                        and value.get("name") == "filesystem.changed"
                        and value.get("tool_id") == "filesystem.write"
                    ):
                        change_value = value.get("value")
                        changes = change_value.get("changes") if isinstance(change_value, dict) else None
                        if isinstance(changes, list):
                            paths = [
                                "Modified: " + " ".join(path.split())
                                for item in changes
                                if isinstance(item, dict)
                                and item.get("action") == "written"
                                and isinstance(path := item.get("path"), str)
                            ]
                            if paths:
                                self._exploration = None
                                row = self._row(block.id, "\n".join(paths), kind="tool")
                                self.transcript.preview(row, terminal_text("\n".join(paths)))
                        continue
                    panel = capability_panel("a13n.harness.tool", {"payload": value}, directory=self.status.directory)
                    if panel is not None:
                        self._row(block.id, f"{panel.title}\n{panel.body}", kind=panel.kind)
                # Unknown capabilities and model instrumentation are not transcript rows.

    def _context_block(self, state: DisplayState, block: DisplayBlock) -> None:
        content = block.content
        value = content.get("value") if block.kind == "extension" else content
        if not isinstance(value, dict):
            return
        operation = value.get("operation_id")
        summary = state.blocks.get(f"{block.scope_id}:context:{operation}") if isinstance(operation, str) else block
        lifecycle = state.blocks.get(f"{block.scope_id}:execution:{operation}") if isinstance(operation, str) else None
        kind = summary.content.get("kind") if summary is not None else value.get("operation")
        title = "Summary" if kind == "handoff" else "Compact"
        status = lifecycle.status if lifecycle is not None else block.status if kind == "provider" else "running"
        if status == "succeeded":
            text = summary.content.get("text", "") if summary is not None else ""
            source = title + "\n" + (str(text) or "Summary content unavailable.")
            files = summary.content.get("files") if summary is not None else None
            if isinstance(files, list):
                paths = [path for path in files if isinstance(path, str)]
                if paths:
                    source += "\n\nFiles to inspect:\n" + "\n".join(paths)
            row_kind = "summary" if title == "Summary" else "compact"
        elif status in {"failed", "cancelled", "unknown"}:
            details = lifecycle.content.get("value") if lifecycle is not None else value
            error = details.get("error_code", status) if isinstance(details, dict) else status
            source, row_kind = f"{title} failed: {error}", "tool"
        else:
            source, row_kind = ("Summarizing context…" if title == "Summary" else "Compacting context…"), "tool"
        key = f"{block.scope_id}:context:{operation}" if isinstance(operation, str) else block.id
        row = self._row(key, source, kind=row_kind)
        if lifecycle is not None:
            self._display_rows[lifecycle.id] = row
            details_row = self._row(
                key + ":details",
                "Context lifecycle\n" + json.dumps(lifecycle.content, ensure_ascii=False, indent=2),
                kind="tool",
            )
            self.transcript.blocks[details_row].concise_hidden = True
        self._exploration = None

    def _tool_block(
        self, block: DisplayBlock, producer_run_id: str, run_id: str, child_label: str, *, visible: bool = True
    ) -> None:
        c = block.content
        name = str(c.get("name") or "tool")[:60]
        call_id = str(c.get("tool_call_id") or block.id)
        key = (run_id, call_id)
        preview = self._tools.get(key)
        fresh = preview is None
        if preview is None:
            preview = self._tools[key] = _ToolPreview(name)
        arguments = c.get("arguments", "")
        arguments = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
        argument_limit = self._limit if self._limit is not None else self.transcript.block_bytes
        arguments_truncated = c.get("truncated") is True or len(arguments) > argument_limit
        detail_arguments = arguments[:argument_limit]
        complete = c.get("arguments_complete") is True
        if complete and name == "ask_user_question" and not child_label and not arguments_truncated:
            self._register_question_arguments(call_id, preview, detail_arguments)
        result = c.get("result")
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        has_result = "result" in c
        result_view = tool_result(name, text) if has_result else ""
        result_state = result_view.partition("\n")[0]
        outcome = c.get("outcome")
        state = (
            "retry"
            if c.get("retry") is True
            else str(outcome)
            if outcome in {"denied", "failed", "interrupted"}
            else result_state
            if has_result
            else "running"
            if block.status in {"pending", "running"}
            else block.status
        )
        receipt = self._questions.get(call_id) if not child_label and name in {"tool", "ask_user_question"} else None
        if has_result and receipt is not None:
            self._question_result(
                receipt,
                text,
                "returned" if outcome in {None, "success"} and state not in {"retry", "failed"} else state,
                producer_run_id,
            )
            for question_key in tuple(self._tools):
                if question_key[1] == call_id and self._tools[question_key].name in {"tool", "ask_user_question"}:
                    self._tools.pop(question_key)
            return
        preview.semantic, preview.read_path = semantic_tool_row(
            name, arguments if complete else "", self.status.directory
        )
        summary = tool_preview(arguments, name=name, directory=self.status.directory) if complete else ""
        if name.startswith("shell"):
            command = self._shell_command(name, arguments, run_id)
            if name in {"shell_exec", "shell_start", "shell_wait"}:
                summary = command
            if has_result:
                self._observe_shell_result(text, command if name in {"shell_exec", "shell_start"} else "", run_id)
        if not visible:
            while len(self._tools) > 128:
                self._tools.pop(next(iter(self._tools)))
            return
        failed = state.startswith("failed") or state in {"retry", "denied", "interrupted", "cancelled"}
        header = f"{name} | {state}" + (f" | {child_label}" if child_label else "")
        provider = c.get("provider")
        if c.get("native") or provider:
            header += f" | provider {provider or 'native'}"
        brief = preview.semantic + (" …" if not has_result and block.status in {"pending", "running"} else "")
        if has_result:
            if name.startswith("shell") and outcome in {None, "success"} and not c.get("retry"):
                brief = "Run " + shell_result_preview(text, summary, separator=" · ")
            elif failed:
                brief = (
                    f"{preview.semantic.split(' ', 1)[0]} {state}: {failure_reason(text, state)} — {preview.semantic}"
                )
            else:
                brief = subagent_result_row(name, preview.semantic, text) or preview.semantic
                if name in {"note_write", "note_delete"} and state in {
                    "created",
                    "updated",
                    "deleted",
                    "already absent",
                }:
                    brief += f" · {state}"
        elif name == "shell_wait":
            brief = "Run waiting · " + (summary or "command unavailable")
        if child_label:
            brief += f" · {child_label}"
        detail_arguments = (
            "" if name in {"edit", "multi_edit", "summarize", "compact"} else tool_arguments(name, detail_arguments)
        )
        if arguments_truncated:
            detail_arguments += "\n[Arguments exceed display budget; /history reads retained content]"
        body = (
            header
            + "\n"
            + (f"Arguments | {call_id}\n{detail_arguments}\n" if detail_arguments else "")
            + (result_view + "\n" if has_result else "")
        )
        metadata = c.get("metadata")
        edit = metadata.get("a13n.harness-ui.applied_edit") if isinstance(metadata, dict) else None
        panel = (
            capability_panel("a13n.filesystem.edit_applied", edit, directory=self.status.directory)
            if isinstance(edit, dict)
            else None
        )
        row_kind = "command" if name.startswith("shell") else "tool"
        if panel is not None:
            row_kind = panel.kind
            body = f"{panel.title}\n{panel.body}\nTool result · {call_id}\n{body}"
            lines = panel.body.splitlines()
            brief = panel.title + "\n" + "\n".join(lines[:100])
            if len(lines) > 100:
                brief += f"\n… {len(lines) - 100} more diff lines · Ctrl+O details"
            if panel.kind != "edit":
                brief = panel.title
            if failed:
                brief += f"\nTool result | {state}"
        row = self._row(block.id, body, kind=row_kind)
        preview.block_id = row
        self.transcript.preview(
            row,
            terminal_text(brief),
            104 if panel is not None else 1,
            limit=self.transcript.block_bytes if panel is not None else 1024,
        )
        self.transcript.blocks[row].concise_hidden = (name in CONTEXT_TOOLS and not failed) or (
            name == "ask_user_question" and not has_result
        )
        if name in EXPLORATION_TOOLS and panel is None:
            group_identity = (run_id, child_label or None)
            if fresh:
                if (
                    self._exploration is None
                    or self._exploration.identity != group_identity
                    or len(self._exploration.members) >= 16
                ):
                    self._exploration = ExplorationGroup(group_identity)
                preview.group = self._exploration
                preview.member = ExplorationMember(row, brief)
                preview.group.members.append(preview.member)
            if preview.group is not None and preview.member is not None:
                preview.member.block_id, preview.member.brief, preview.member.read_path = (
                    row,
                    terminal_text(brief),
                    preview.read_path,
                )
                preview.member.active, preview.member.failed = not has_result, failed
                preview.group.refresh(self.transcript)
        else:
            self._exploration = None
        if not child_label and self.status.state != "cancelling":
            self.status.state = "working" if has_result else name
        while len(self._tools) > 128:
            self._tools.pop(next(iter(self._tools)))

    def ingest_control(
        self,
        event_type: str,
        payload: Mapping[str, object] | None,
        *,
        child: bool = False,
        run_id: str = "root",
        execution_id: str | None = None,
    ) -> None:
        """Small lifecycle and process controls are not transcript reconstruction."""
        if payload is None:
            self.gap = True
            return
        if event_type in {"RUN_FINISHED", "RUN_ERROR"}:
            self._exploration = None
            self.end_process_observations(run_id)
        if event_type == "RUN_ERROR":
            self.finish()
            self.append(f"Error: {payload.get('message', payload.get('code', 'run failed'))}\n")
            return
        if event_type != "CUSTOM":
            return
        value = payload.get("value")
        event = value.get("event") if isinstance(value, dict) else None
        if not isinstance(event, dict):
            return
        visible = not child or self.status.mode == "detailed"
        name = payload.get("name")
        if name == "a13n.harness.recovery":
            recovery = event.get("payload")
            if visible and isinstance(recovery, dict) and recovery.get("type") == "model_retry_scheduled":
                label = f" · {execution_id or run_id}" if child else ""
                self.finish()
                self.append(f"[System{label}] Retrying model request…\n", kind="notice")
        elif name == "a13n.shell.status":
            process_id, phase = event.get("process_id"), event.get("phase")
            if isinstance(process_id, str) and isinstance(phase, str):
                observation = self._shell_observation(run_id, process_id)
                if phase != "running" or not observation.completion_seen:
                    observation.phase = phase
                if type(event.get("exit_code")) is int:
                    observation.exit_code = event["exit_code"]
            if visible:
                self._shell_notification(event, run_id, (execution_id or run_id) if child else "")
