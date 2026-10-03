"""Semantic stream presentation with bounded per-message display state."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from a13n_stream_protocol import ContentMetadata
from a13n_stream_protocol.display import AppendItem, Item, ItemChange, apply_changes

from .context_activity import ContextActivity
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
    started: float
    arguments: str = ""
    size: int = 0
    truncated: bool = False
    block_id: int | None = None
    summary: str = ""
    edit_applied: bool = False
    native_result_seen: bool = False
    protocol_result_seen: bool = False
    semantic: str = ""
    read_path: str | None = None
    group: ExplorationGroup | None = None
    member: ExplorationMember | None = None


@dataclass(slots=True)
class _QuestionResult:
    block_id: int | None = None
    native_seen: bool = False
    protocol_seen: bool = False


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
    """Keep only a bounded pending batch and bounded per-tool argument tails.

    The App owns durable history. This adapter retains bounded semantic blocks
    and a bounded drain buffer for observation/testing, not execution authority.
    """

    def __init__(self, status: Status, *, limit: int | None = None) -> None:
        from .tasks import TaskPanel

        self.status = status
        self.transcript = Transcript()
        self.tasks = TaskPanel()
        self._notes: NotePage | None = None
        self._local_inputs: dict[str, int] = {}
        self._composer_inputs: dict[tuple[str, str], tuple[int, set[int]]] = {}
        self._messages: dict[tuple[str, str, str], int] = {}
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
        self._items: dict[str, Item] = {}
        self._item_sizes: dict[str, int] = {}
        self._exploration: ExplorationGroup | None = None
        self._context: dict[tuple[str, str], ContextActivity] = {}
        self._write_notices: dict[tuple[str, str, str], None] = {}
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

    def _register_question_arguments(self, call_id: str, preview: _ToolPreview) -> None:
        """Recognize replayed calls only by the public tool name and request schema."""
        from a13n_harness.capabilities import AskUserQuestionRequest
        from pydantic import ValidationError

        from a13n_harness_ui.surfaces import QuestionView, StructuredQuestionRequestView

        if preview.truncated:
            return
        try:
            request = AskUserQuestionRequest.model_validate_json(preview.arguments)
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
            receipt.arguments = preview.arguments
            self._trim_questions()

    def _question_result(self, receipt: _QuestionReceipt, text: str, native_state: str | None, run_id: str) -> None:
        """Deduplicate observations within one response attempt, not all future retries."""
        from a13n_harness.toolsets.interaction import validate_user_question_result

        attempt = receipt.attempts.get(run_id)
        if attempt is None:
            attempt = _QuestionResult(receipt.block_id if not receipt.attempts else None)
            receipt.attempts[run_id] = attempt
            while len(receipt.attempts) > 16:
                receipt.attempts.pop(next(iter(receipt.attempts)))
        native = native_state is not None
        if (native and attempt.native_seen) or (not native and attempt.protocol_seen):
            return
        already_seen = attempt.native_seen or attempt.protocol_seen
        # A native failure cannot be overwritten by a later success projection.
        update_preview = native or not attempt.native_seen
        attempt.native_seen |= native
        attempt.protocol_seen |= not native
        try:
            value = json.loads(text)
        except ValueError:
            value = None
        content = value.get("content") if native and isinstance(value, dict) else value
        state = native_state or "returned"
        if native and (
            not isinstance(value, dict)
            or value.get("part_kind") != "tool-return"
            or value.get("outcome", "success") != "success"
        ):
            state = native_state if native_state != "returned" else "unavailable"
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
                answer_text = content if isinstance(content, str) else failure_reason(text, state)
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
        detail = terminal_text(f"{'Native result' if native else 'Tool result'}\n{text}\n")
        block_id = attempt.block_id
        if not already_seen or block_id not in self.transcript.blocks:
            body = (
                terminal_text(
                    f"ask_user_question | {state}\nArguments | {receipt.request.request_id}\n{receipt.arguments}\n"
                )
                + detail
            )
            if block_id is None or not self.transcript.replace(block_id, body, kind="question_receipt"):
                block_id = self.transcript.append(body, kind="question_receipt")
            attempt.block_id = block_id
        else:
            self.transcript.extend(block_id, "\n" + detail)
        if update_preview:
            self.transcript.preview(block_id, brief, 64, limit=self.transcript.block_bytes)
        self.transcript.blocks[block_id].concise_hidden = False
        if not already_seen:
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

    def ingest(
        self,
        changes: Sequence[ItemChange] | None,
        *,
        child: bool = False,
        run_id: str = "root",
        execution_id: str | None = None,
    ) -> None:
        """Apply Host-owned compact changes; raw observer events are not presentation input."""
        if changes is None:
            self.gap = True
            return
        for change in changes:
            item_id = change.id if isinstance(change, AppendItem) else change.item.id
            previous = self._items.get(item_id)
            if isinstance(change, AppendItem) and previous is None:
                self.gap = True
                continue
            apply_changes(self._items, (change,))
            item = self._items[item_id]
            budget = self._limit or self.transcript.block_bytes
            content = dict(item.content)
            for name in ("text", "arguments", "result"):
                value = content.get(name)
                if isinstance(value, str) and len(value) > budget:
                    content[name] = value[:budget]
                    content["truncated"] = True
            item = self._items[item_id] = item.model_copy(update={"content": content})
            self._item_sizes[item_id] = len(item.model_dump_json())
            if previous is not None and previous.last_stream_id == item.last_stream_id:
                continue
            scope = item.content.get("subagentRunId")
            nested = child or isinstance(scope, str)
            lane = scope if isinstance(scope, str) else run_id
            identity = terminal_text(execution_id or lane)
            if item.kind in {"text_message", "reasoning_message"}:
                self._message(item, previous, child=nested, run_id=lane, identity=identity)
            elif item.kind == "tool_call":
                self._tool_item(item, previous, child=nested, run_id=lane, identity=identity)
            elif previous is None or previous.content != item.content:
                self._observation(item.content, child=nested, run_id=lane, identity=identity)
            # The transcript owns the bounded visible content. Only a bounded live
            # working set is needed to apply subsequent appends and replacements.
            while self._items and (
                len(self._items) > 128 or sum(self._item_sizes.values()) > self.transcript.max_bytes
            ):
                oldest = next(iter(self._items))
                self._items.pop(oldest)
                self._item_sizes.pop(oldest)

    def _message(self, item: Item, previous: Item | None, *, child: bool, run_id: str, identity: str) -> None:
        content = item.content
        metadata = ContentMetadata.from_native(content.get("metadata"))
        user = content.get("role") == "user"
        if not metadata.display or (child and self.status.mode != "detailed"):
            return
        if user and not child and metadata.source_id in self._local_inputs:
            return
        thinking = item.kind == "reasoning_message"
        text = str(content.get("text", ""))
        if content.get("truncated"):
            text += "\n[Content exceeds display budget; /history reads retained content]"
        media = content.get("input_media")
        if isinstance(media, dict):
            label = str(media.get("media_type") or media.get("kind") or "media")
            if isinstance(media.get("size_bytes"), int):
                label += f" · {media['size_bytes']:,} bytes"
            reference = media.get("url") or media.get("file_id")
            if isinstance(reference, str):
                label += f" · {reference}"
            text = f"[{label}]"
        piece = composer_piece(metadata) if user else None
        if piece is not None:
            assert metadata.source_id is not None
            index, label = piece
            text = f"[{label}]" if label else text
            key = (run_id, metadata.source_id)
            state = self._composer_inputs.get(key)
            if state is not None and index in state[1]:
                return
            if not text:
                return
            self.finish()
            before = str(previous.content.get("text", "")) if previous is not None else ""
            delta = text[len(before) :] if not label and not media and text.startswith(before) else text
            if state is None or not self.transcript.extend(state[0], terminal_text(delta)):
                prefix = f"> Subagent {identity} · " if child else "> "
                state = (self.transcript.append(prefix + terminal_text(text), kind="user"), set())
                self._composer_inputs[key] = state
            if item.state != "in_progress":
                state[1].add(index)
                self.transcript.complete(state[0])
            while len(self._composer_inputs) > 128:
                self._composer_inputs.pop(next(iter(self._composer_inputs)))
            self.append(delta, display=False)
            return
        self._exploration = None
        if not user and not child and self.status.state != "cancelling":
            self.status.state = "thinking" if thinking else "responding"
        notification = user and (metadata.model_extra or {}).get("a13n.steering-source") in {
            "background_process",
            "async_subagent",
        }
        key = (run_id, item.id, "thinking" if thinking else "user" if user else "assistant")
        block_id = self._messages.get(key)
        label = (
            "Activity · "
            if notification
            else (f"**Subagent · {identity}**\n\n" if child else "") + ("> " if user else "")
        )
        if text:
            if block_id is None or not self.transcript.replace(block_id, terminal_text(label + text)):
                self.finish()
                block_id = self.transcript.append(
                    terminal_text(label + text),
                    markdown=not user,
                    streaming=item.state == "in_progress",
                    kind="tool" if notification else "thinking" if thinking else "user" if user else "text",
                )
                self._messages[key] = block_id
            before = str(previous.content.get("text", "")) if previous is not None else ""
            if previous is not None and previous.content.get("truncated"):
                before += "\n[Content exceeds display budget; /history reads retained content]"
            self.append(text[len(before) :] if text.startswith(before) else text, display=False)
            if not user and not thinking and not child:
                self.assistant_seen = True
        if item.state != "in_progress":
            if block_id is not None:
                self.transcript.complete(block_id)
            self._messages.pop(key, None)
            self.finish()
        while len(self._messages) > 128:
            self._messages.pop(next(iter(self._messages)))

    def _tool_item(self, item: Item, previous: Item | None, *, child: bool, run_id: str, identity: str) -> None:
        content = item.content
        if not ContentMetadata.from_native(content.get("metadata")).display:
            return
        before = previous.content if previous is not None else {}
        if previous is None and not any(key in content for key in ("value", "result", "result_parts")):
            self._tool("start", content, child=child, run_id=run_id, identity=identity)
        if content.get("arguments_complete") and (
            not before.get("arguments_complete") or content.get("arguments") != before.get("arguments")
        ):
            self._tool("ready", content, child=child, run_id=run_id, identity=identity)
        edit = content.get("applied_edit")
        if isinstance(edit, dict) and edit != before.get("applied_edit"):
            self._observation(
                {
                    "name": "a13n.filesystem.edit_applied",
                    "value": {
                        "event": {
                            **edit,
                            "tool_call_id": content.get("toolCallId"),
                        }
                    },
                },
                child=child,
                run_id=run_id,
                identity=identity,
            )
        if "value" in content and (
            "value" not in before or any(content.get(k) != before.get(k) for k in ("value", "outcome", "retry"))
        ):
            outcome = content.get("outcome")
            state = "retry" if content.get("retry") else str(outcome) if outcome in {"failed", "denied"} else "returned"
            self._tool(
                "result",
                content,
                text=json.dumps(
                    {
                        "part_kind": "retry-prompt" if content.get("retry") else "tool-return",
                        "tool_name": content.get("toolCallName"),
                        "tool_call_id": content.get("toolCallId"),
                        "content": content["value"],
                        "outcome": content.get("outcome") or "success",
                    },
                    ensure_ascii=False,
                ),
                native_state=state,
                child=child,
                run_id=run_id,
                identity=identity,
            )
        elif "value" not in content:
            field = "result_parts" if "result_parts" in content else "result"
            if field in content and (field not in before or content[field] != before[field]):
                text = (
                    json.dumps(content[field], ensure_ascii=False) if field == "result_parts" else str(content[field])
                )
                self._tool("result", content, text=text, child=child, run_id=run_id, identity=identity)

    def _tool(
        self,
        action: Literal["start", "ready", "result"],
        content: Mapping[str, object],
        *,
        child: bool,
        run_id: str,
        identity: str,
        text: str = "",
        native_state: str | None = None,
    ) -> None:
        detailed = self.status.mode == "detailed"
        raw_call_id = str(content.get("toolCallId", "unknown"))
        call_id = terminal_text(raw_call_id)
        key = (run_id, call_id)
        receipt = (
            self._questions.get(raw_call_id)
            if not child and content.get("toolCallName") in (None, "ask_user_question")
            else None
        )
        if action == "result" and receipt is not None:
            self._question_result(receipt, text, native_state, run_id)
            for tool_key in tuple(self._tools):
                if tool_key[1] == call_id and self._tools[tool_key].name == "ask_user_question":
                    self._tools.pop(tool_key)
            if self.status.state != "cancelling":
                self.status.state = "working"
            self.boundary = True
            return
        preview = self._tools.get(key)
        label = f"{identity} / {call_id}" if child else call_id
        if action == "start":
            if self._exploration is not None and self._exploration.members:
                if next(reversed(self.transcript.blocks), None) != self._exploration.members[-1].block_id:
                    self._exploration = None
            preview = _ToolPreview(str(content.get("toolCallName", "tool"))[:60], time.monotonic())
            preview.semantic, preview.read_path = semantic_tool_row(preview.name, "", self.status.directory)
            if preview.name not in EXPLORATION_TOOLS:
                self._exploration = None
            self._tools[key] = preview
            if not child and self.status.state != "cancelling":
                self.status.state = preview.name
            if (not child or detailed) and (preview.name != "ask_user_question" or detailed):
                self.finish()
                header = f"{preview.name} | running" + (f" | {identity}" if child else "")
                shell = preview.name.startswith("shell")
                preview.block_id = self.transcript.append(
                    header + "\n", collapsed_lines=None if shell else 1, kind="command" if shell else "tool"
                )
                brief = preview.semantic + " …"
                if preview.name in {"edit", "multi_edit"}:
                    brief = header
                self.transcript.preview(preview.block_id, brief)
                if preview.name in CONTEXT_TOOLS or preview.name == "ask_user_question":
                    self.transcript.blocks[preview.block_id].concise_hidden = True
                elif preview.name in EXPLORATION_TOOLS:
                    group_identity = (run_id, identity if child else None)
                    if (
                        self._exploration is None
                        or self._exploration.identity != group_identity
                        or len(self._exploration.members) >= 16
                    ):
                        self._exploration = ExplorationGroup(group_identity)
                    preview.group = self._exploration
                    preview.member = ExplorationMember(preview.block_id, brief)
                    preview.group.members.append(preview.member)
                    preview.group.refresh(self.transcript)
                self.append(header + "\n", display=False)
        elif action == "result":
            name = preview.name if preview else str(content.get("toolCallName", "tool"))[:60]
            subagent_receipt = preview is not None and name in {"delegate", "steer_subagent"}
            if subagent_receipt and preview is not None:
                if (native_state is not None and preview.native_result_seen) or (
                    native_state is None and preview.protocol_result_seen
                ):
                    return
            if name.startswith("shell"):
                command = self._shell_command(name, preview.arguments, run_id) if preview else ""
                if preview and name in {"shell_exec", "shell_start", "shell_wait"}:
                    preview.summary = command
                self._observe_shell_result(text, command if name in {"shell_exec", "shell_start"} else "", run_id)
            if not child or detailed:
                elapsed = f" | {time.monotonic() - preview.started:.1f}s" if preview else ""
                result = tool_result(name, text)
                state, _, output = result.partition("\n")
                if native_state is not None:
                    state = native_state
                    result = f"{state}\n{output}"
                header = f"{name} | {state}" + (f" | {identity}" if child else "")
                summary = preview.summary if preview else ""
                brief = header + (f" | {' '.join(summary.split())}" if summary else "") + elapsed
                shell_preview = None
                if name.startswith("shell") and native_state is None:
                    shell_preview = shell_result_preview(text, summary, separator=" · ")
                if shell_preview is not None:
                    brief = f"{name} | {shell_preview}"
                    if child:
                        brief += f" | {identity}"
                failed = state.startswith("failed") or state in {"retry", "denied"}
                if name not in {"edit", "multi_edit"}:
                    semantic = preview.semantic if preview else semantic_tool_row(name, "", self.status.directory)[0]
                    if shell_preview is not None:
                        brief = "Run " + shell_preview
                    elif failed:
                        brief = f"{semantic.split(' ', 1)[0]} {state}: {failure_reason(text, state)} — {semantic}"
                    else:
                        brief = subagent_result_row(name, semantic, text) or semantic
                        if name in {"note_write", "note_delete"} and state in {
                            "created",
                            "updated",
                            "deleted",
                            "already absent",
                        }:
                            brief += f" · {state}"
                    if child:
                        brief += f" · {identity}"
                arguments = preview.arguments if preview else ""
                body = (
                    header
                    + elapsed
                    + "\n"
                    + (f"Arguments | {label}\n{arguments}\n" if arguments else "")
                    + result
                    + "\n"
                )
                block_id = preview.block_id if preview is not None else None
                applied_retained = (
                    preview is not None
                    and preview.edit_applied
                    and block_id is not None
                    and self.transcript.extend(block_id, terminal_text(f"\nTool result · {label}\n{result}\n"))
                )
                if (
                    applied_retained
                    and block_id is not None
                    and (state.startswith("failed") or state in {"retry", "denied"})
                ):
                    block = self.transcript.blocks[block_id]
                    self.transcript.preview(
                        block_id,
                        (block.preview or "Edit applied") + f"\nTool result | {state}",
                        54,
                        limit=self.transcript.block_bytes,
                    )
                if not applied_retained:
                    kind = "command" if name.startswith("shell") else "tool"
                    retained_subagent = (
                        subagent_receipt
                        and preview is not None
                        and (preview.native_result_seen or preview.protocol_result_seen)
                        and block_id is not None
                        and self.transcript.extend(block_id, terminal_text(f"\nAdditional tool result\n{result}\n"))
                    )
                    if not retained_subagent:
                        if block_id is None or not self.transcript.replace(block_id, terminal_text(body), kind=kind):
                            block_id = self.transcript.append(terminal_text(body), collapsed_lines=1, kind=kind)
                    assert block_id is not None
                    if not (
                        retained_subagent
                        and preview is not None
                        and preview.native_result_seen
                        and native_state is None
                    ):
                        self.transcript.preview(block_id, terminal_text(brief), 1)
                    if subagent_receipt and preview is not None:
                        preview.block_id = block_id
                    if name in CONTEXT_TOOLS:
                        self.transcript.blocks[block_id].concise_hidden = not failed
                    elif name == "ask_user_question":
                        self.transcript.blocks[block_id].concise_hidden = False
                    if preview is not None and preview.group is not None and preview.member is not None:
                        preview.member.block_id = block_id
                        preview.member.brief = terminal_text(brief)
                        preview.member.active = False
                        preview.member.failed = failed
                        preview.group.refresh(self.transcript)
                self.append(header + elapsed + "\n", display=False)
            if subagent_receipt and preview is not None:
                # These receipts have both native and protocol observations;
                # keep bounded correlation without treating delivery as completion.
                preview.native_result_seen |= native_state is not None
                preview.protocol_result_seen |= native_state is None
                preview.arguments = ""
                preview.size = 0
            else:
                self._tools.pop(key, None)
            if not child and self.status.state != "cancelling":
                self.status.state = "working"
            self.boundary = True
        elif action == "ready":
            # END completes argument generation, not tool execution.
            if preview:
                preview.arguments = str(content.get("arguments", ""))
                budget = self._limit or self.transcript.block_bytes
                preview.truncated = content.get("truncated") is True or len(preview.arguments) > budget
                preview.arguments = preview.arguments[:budget]
                if preview.name == "ask_user_question" and not child:
                    self._register_question_arguments(raw_call_id, preview)
                preview.semantic, preview.read_path = semantic_tool_row(
                    preview.name, preview.arguments, self.status.directory
                )
                preview.summary = tool_preview(preview.arguments, name=preview.name, directory=self.status.directory)
                if preview.name in {"shell_exec", "shell_start", "shell_wait"}:
                    preview.summary = self._shell_command(preview.name, preview.arguments, run_id)
                if preview.name in {"edit", "multi_edit", "summarize", "compact"}:
                    preview.arguments = ""
                elif preview.arguments:
                    preview.arguments = tool_arguments(preview.name, preview.arguments)
                    if preview.truncated:
                        preview.arguments += "\n[Arguments exceed display budget; /history reads retained content]"
                preview.size = len(preview.arguments)
                if preview.block_id is not None and preview.arguments:
                    header = f"{preview.name} | running" + (f" | {identity}" if child else "")
                    self.transcript.replace(preview.block_id, terminal_text(header + "\n" + preview.arguments))
                    brief = header + (f" | {preview.summary}" if preview.summary else "")
                    if preview.name.startswith("shell"):
                        state = "waiting" if preview.name == "shell_wait" else "running"
                        brief = f"{preview.name} | {state} | {preview.summary or 'command unavailable'}"
                        if child:
                            brief += f" | {identity}"
                    if preview.name not in {"edit", "multi_edit"}:
                        brief = preview.semantic + " …"
                        if preview.name == "shell_wait":
                            brief = "Run waiting · " + (preview.summary or "command unavailable")
                    self.transcript.preview(preview.block_id, terminal_text(brief), 1)
                    if preview.group is not None and preview.member is not None:
                        preview.member.brief = terminal_text(brief)
                        preview.member.read_path = preview.read_path
                        preview.group.refresh(self.transcript)
            self.boundary = True
        # START-only and malformed streams obey the same bound as ARGS.
        while len(self._tools) > 128:
            self._tools.pop(next(iter(self._tools)))
        return

    def _observation(self, content: Mapping[str, object], *, child: bool, run_id: str, identity: str) -> None:
        if not ContentMetadata.from_native(content.get("metadata")).display:
            return
        detailed = self.status.mode == "detailed"
        lifecycle = content.get("event")
        if isinstance(lifecycle, dict):
            kind = lifecycle.get("type")
            if kind in {"RUN_FINISHED", "RUN_ERROR", "SUBAGENT_FINISHED", "SUBAGENT_ERROR"}:
                self._exploration = None
                self.end_process_observations(run_id)
            if kind == "RUN_FINISHED":
                outcome = lifecycle.get("outcome")
                if isinstance(outcome, dict) and outcome.get("type") in {"cancelled", "interrupt"}:
                    self.append(
                        "Execution cancelled.\n"
                        if outcome["type"] == "cancelled"
                        else "Execution suspended: a response is needed.\n"
                    )
            if kind == "RUN_ERROR":
                self.finish()
                self.append(f"Error: {lifecycle.get('message', lifecycle.get('code', 'run failed'))}\n")
            return
        value = content.get("value")
        event = value.get("event") if isinstance(value, dict) else None
        if not isinstance(event, dict):
            return
        name = content.get("name")
        if name == "a13n.harness.recovery":
            recovery = event.get("payload")
            if isinstance(recovery, dict) and recovery.get("type") == "model_retry_scheduled":
                if not child or detailed:
                    self.finish()
                    label = f" · {identity}" if child else ""
                    self.append(f"[System{label}] Retrying model request…\n", kind="notice")
            return
        if name == "a13n.shell.status":
            process_id, phase = event.get("process_id"), event.get("phase")
            if isinstance(process_id, str) and isinstance(phase, str):
                observation = self._shell_observation(run_id, process_id)
                observation.phase = phase
                if type(event.get("exit_code")) is int:
                    observation.exit_code = event["exit_code"]
            if not child or detailed:
                self._shell_notification(event, run_id, identity if child else "")
            # Recognized routine statuses are intentionally quiet, not
            # unknown capability events to render as raw JSON.
            return
        mutation = event.get("payload")
        context_content = name in {"a13n.context.compaction_summary", "a13n.context.handoff_summary"}
        context_kind = str(mutation.get("type", "")) if isinstance(mutation, dict) else ""
        if context_content or (name == "a13n.harness.context" and context_kind.startswith(("compaction_", "handoff_"))):
            self._exploration = None
            if not child or detailed:
                observation = event if context_content else mutation
                assert isinstance(observation, dict)
                operation_id = observation.get("operation_id")
                if isinstance(operation_id, str):
                    context_key = (run_id, operation_id)
                    activity = self._context.get(context_key)
                    if activity is None or activity.block_id not in self.transcript.blocks:
                        block_id = self.transcript.append("", kind="tool")
                        details_id = self.transcript.append("", kind="tool")
                        self.transcript.blocks[details_id].concise_hidden = True
                        title = (
                            "Compact"
                            if (context_kind.startswith("compaction_") or name == "a13n.context.compaction_summary")
                            else "Summary"
                        )
                        activity = self._context[context_key] = ContextActivity(title, block_id, details_id)
                    activity.update(self.transcript, str(name) if context_content else context_kind, observation)
                    # All rendered native content crosses the same terminal-text boundary.
                    for block_id in (activity.block_id, activity.details_id):
                        block = self.transcript.blocks.get(block_id)
                        if block is not None:
                            self.transcript.replace(block_id, terminal_text(block.source))
                    while len(self._context) > 64:
                        self._context.pop(next(iter(self._context)))
            return
        if (
            name == "a13n.harness.tool"
            and isinstance(mutation, dict)
            and mutation.get("type") == "tool_extra"
            and mutation.get("name") == "filesystem.changed"
            and mutation.get("tool_id") == "filesystem.write"
        ):
            if not child or detailed:
                value = mutation.get("value")
                changes = value.get("changes") if isinstance(value, dict) else None
                if isinstance(changes, list):
                    for change in changes:
                        if (
                            not isinstance(change, dict)
                            or change.get("action") != "written"
                            or not isinstance(change.get("path"), str)
                        ):
                            continue
                        notice_key = (run_id, str(mutation.get("tool_call_id")), change["path"])
                        if notice_key not in self._write_notices:
                            self._write_notices[notice_key] = None
                            notice = terminal_text("Modified: " + " ".join(change["path"].split()))
                            block_id = self.transcript.append(notice, kind="tool")
                            self.transcript.preview(block_id, notice)
                    while len(self._write_notices) > 128:
                        self._write_notices.pop(next(iter(self._write_notices)))
            return
        panel = capability_panel(name, event, directory=self.status.directory)
        if panel is not None:
            if not child or detailed:
                self.finish()
                source = terminal_text(f"{panel.title}\n{panel.body}\n")
                edit = (
                    self._tools.get((run_id, str(event.get("tool_call_id"))))
                    if name == "a13n.filesystem.edit_applied"
                    else None
                )
                block_id = edit.block_id if edit is not None else None
                if block_id is None or not self.transcript.replace(block_id, source, kind=panel.kind):
                    block_id = self.transcript.append(source, kind=panel.kind)
                if edit is not None:
                    edit.block_id = block_id
                    edit.edit_applied = True
                if panel.kind == "edit":
                    lines = panel.body.splitlines()
                    preview_body = "\n".join(lines[:100])
                    if len(lines) > 100:
                        preview_body += f"\n… {len(lines) - 100} more diff lines · Ctrl+O details"
                    self.transcript.preview(
                        block_id,
                        terminal_text(f"{panel.title}\n{preview_body}"),
                        104,
                        limit=self.transcript.block_bytes,
                    )
                elif panel.kind == "tool":
                    self.transcript.preview(block_id, terminal_text(panel.title))
                self.append(source, display=False)
            return
        if event.get("event_kind") == "capability":
            # Only explicitly handled capabilities produce conversation content.
            return
        mutation = event.get("payload")
        if isinstance(mutation, dict):
            kind = str(mutation.get("type", ""))
            if kind == "task_changed" and not child:
                self.tasks.ingest(mutation)
        if name == "a13n.pydantic_ai.enqueued_messages" and event.get("event_kind") == "enqueued_messages":
            # Typed input observations own applied input. Acceptance is a local
            # notification; queue/delivery facts must not echo it again.
            return
