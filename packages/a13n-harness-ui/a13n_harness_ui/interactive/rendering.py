"""Semantic stream presentation with bounded per-message display state."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from .panels import capability_panel, shell_outcome, shell_result_preview, tool_arguments, tool_preview, tool_result
from .transcript import Transcript

if TYPE_CHECKING:
    from a13n_harness.usage import BoundedRequestUsage, ModelUsageRecord

    from a13n_harness_ui.storage.usage import UsageTotals
    from a13n_harness_ui.surfaces import NotePage

    from .local_shell import LocalShellEvent


def terminal_text(value: str) -> str:
    """Render untrusted content as text, never as terminal control sequences."""
    return "".join(char for char in value if char in "\n\t" or (ord(char) >= 32 and not 127 <= ord(char) <= 159))


@dataclass(slots=True)
class Status:
    theme: Literal["auto", "dark", "light"] = "auto"
    theme_explicit: bool = False
    state: str = "starting"
    agent: str = "not configured"
    model: str = "not configured"
    thinking: str = "default"
    environment: str = "not selected"
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
    _usage_ids: set[str] = field(default_factory=set)

    def reset_usage(self) -> None:
        self.usage = None
        self.requests = 0
        self._usage_ids.clear()

    def restore_usage(self, totals: UsageTotals) -> None:
        """Replace the Thread baseline; live IDs belong only to the next operation."""
        from a13n_harness.usage import BoundedRequestUsage

        self.reset_usage()
        self.requests = totals.model_requests
        if self.requests:
            self.usage = BoundedRequestUsage.model_validate(
                {**dict(totals.tokens), "cost": None if totals.unknown_model_costs else totals.model_cost_usd}
            )

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
        cost = current.cost
        if previous is not None:
            cost = None if cost is None or previous.cost is None else previous.cost + cost
        self.usage = BoundedRequestUsage(**values, cost=cost)

    @property
    def cache_rate(self) -> float | None:
        if self.usage is None:
            return None
        total = self.usage.input_tokens + self.usage.output_tokens
        return 100 * self.usage.cache_read_tokens / total if total else None

    def usage_details(self) -> str:
        if self.usage is None:
            return "Root Thread usage: unavailable (no observed model response)."
        usage = self.usage
        cost = "unknown" if usage.cost is None else f"USD {usage.cost:.6f} (model estimate, not subscription billing)"
        return (
            f"Observed root Thread: {self.requests} requests · input {usage.input_tokens:,} · output {usage.output_tokens:,}\n"
            f"Cache read {usage.cache_read_tokens:,} · cache write {usage.cache_write_tokens:,} (provider-reported counters)\n"
            f"Cache rate: {self.cache_rate_text} of input + output. "
            f"Cost: {cost}. Child and non-model usage excluded."
        )

    @property
    def cache_rate_text(self) -> str:
        return "--" if self.cache_rate is None else f"{self.cache_rate:.1f}%"

    def line(self, width: int | None = None) -> str:
        elapsed = time.monotonic() - self.started if self.started is not None else self.elapsed
        from prompt_toolkit.utils import get_cwidth

        context = "--" if self.context_tokens is None else f"{self.context_tokens:,}"
        if self.context_tokens is not None and self.context_window:
            context += f" ({100 * self.context_tokens / self.context_window:.0f}%)"
        cost = "cost --" if self.usage is None or self.usage.cost is None else f"${self.usage.cost:.4f}"
        fields = [
            self.state.capitalize(),
            f"ctx {context}",
            f"cache {self.cache_rate_text}",
            cost,
            self.model.split(":")[-1],
            f"{elapsed:.0f}s",
        ]
        while width is not None and get_cwidth(" · ".join(fields)) + 2 > width and len(fields) > 1:
            fields.pop()
        return terminal_text(" " + " · ".join(fields) + " ")


@dataclass(slots=True)
class _ToolPreview:
    name: str
    started: float
    arguments: str = ""
    parts: list[str] | None = None
    size: int = 0
    truncated: bool = False
    block_id: int | None = None
    summary: str = ""


@dataclass(slots=True)
class _ShellObservation:
    command: str = ""
    completion_seen: bool = False
    phase: str = "unknown"
    background: bool = False


class StreamRenderer:
    """Keep only a bounded pending batch and bounded per-tool argument tails.

    The App owns durable history. This adapter retains bounded semantic blocks
    and a bounded drain buffer for observation/testing, not execution authority.
    """

    def __init__(self, status: Status, *, limit: int | None = None) -> None:
        from a13n_stream_protocol import CustomEventAssembler

        from .tasks import TaskPanel

        self.status = status
        self.transcript = Transcript()
        self.tasks = TaskPanel()
        self._notes: NotePage | None = None
        self._local_inputs: dict[str, int] = {}
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
        self._custom_events = CustomEventAssembler()

    def _shell_observation(self, run_id: str, process_id: str) -> _ShellObservation:
        key = (run_id, process_id)
        observation = self._shell_processes.setdefault(key, _ShellObservation())
        while len(self._shell_processes) > 128:
            self._shell_processes.pop(next(iter(self._shell_processes)))
            self._shell_observations_omitted = True
        return observation

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
        lines = ["Background processes · live observations, not a host process list"]
        processes = [(key, value) for key, value in self._shell_processes.items() if value.background]
        for (run_id, process_id), item in processes[-16:]:
            lines.append(f"{process_id} · {item.phase} · {item.command or 'command unavailable'} · Run {run_id}")
        if not processes:
            lines.append("No background processes observed in this conversation.")
        if len(processes) > 16 or self._shell_observations_omitted:
            lines.append("Older observations omitted; showing at most 16 processes.")
        if self.gap:
            lines.append("Live output was incomplete; process observations may be stale or missing.")
        lines.append("Status is last observed; unavailable does not confirm exit. Expand tool details for output.")
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
        self.append("\n".join(lines).rstrip() + "\n", kind="notes")

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
        event_type: str,
        payload: Mapping[str, object] | None,
        *,
        child: bool = False,
        run_id: str = "root",
        execution_id: str | None = None,
    ) -> None:
        from a13n_stream_protocol import ContentMetadata

        if payload is None:
            self.gap = True
            return
        if event_type == "CUSTOM":
            payload = self._custom_events.accept(payload)
            self.gap |= self._custom_events.gap
            if payload is None:
                return
        metadata = ContentMetadata.from_native(payload.get("metadata"))
        if not metadata.display:
            return
        detailed = self.status.mode == "detailed"
        delta = payload.get("delta") or payload.get("content") or ""
        text = delta if isinstance(delta, str) else json.dumps(delta, ensure_ascii=False)
        thinking = event_type.startswith(("REASONING_MESSAGE", "THINKING_TEXT_MESSAGE"))
        message = event_type.startswith("TEXT_MESSAGE")
        user = message and payload.get("role") == "user"
        if user and not child and metadata.source_id in self._local_inputs:
            # Correlate explicit authored input identity, never equal text. Keep
            # the identity across repeated model boundaries of this operation.
            return
        assistant = message and not user
        identity = terminal_text(execution_id or run_id)
        if thinking or message:
            if not user and not child and self.status.state != "cancelling":
                self.status.state = "thinking" if thinking else "responding"
            if child and not detailed:
                return
            key = (
                run_id,
                str(payload.get("message_id", "default")),
                "thinking" if thinking else "user" if user else "assistant",
            )
            if event_type.endswith("START"):
                self.finish()
                self._messages.pop(key, None)
            elif event_type.endswith("END"):
                block_id = self._messages.pop(key, None)
                if block_id is not None:
                    self.transcript.complete(block_id)
                self.finish()
            elif text:
                block_id = self._messages.get(key)
                if block_id is None or not self.transcript.extend(block_id, terminal_text(text)):
                    label = (f"**Subagent · {identity}**\n\n" if child else "") + ("> " if user else "")
                    self._messages[key] = self.transcript.append(
                        label + terminal_text(text),
                        markdown=not user,
                        streaming=True,
                        kind="thinking" if thinking else "user" if user else "text",
                    )
                    if len(self._messages) > 128:
                        self._messages.pop(next(iter(self._messages)))
                self.append(text, display=False)
                if assistant and not child:
                    self.assistant_seen = True
            return
        if event_type.startswith("TOOL_CALL"):
            call_id = terminal_text(str(payload.get("tool_call_id", "unknown")))
            key = (run_id, call_id)
            preview = self._tools.get(key)
            label = f"{identity} / {call_id}" if child else call_id
            if event_type.endswith("START"):
                preview = _ToolPreview(str(payload.get("tool_call_name", "tool"))[:60], time.monotonic())
                self._tools[key] = preview
                if not child and self.status.state != "cancelling":
                    self.status.state = preview.name
                if (not child or detailed) and (preview.name != "ask_user_question" or detailed):
                    self.finish()
                    header = f"{preview.name} · running" + (f" · {identity}" if child else "")
                    preview.block_id = self.transcript.append(header + "\n", collapsed_lines=1, kind="tool")
                    self.append(header + "\n", display=False)
            elif event_type.endswith(("ARGS", "CHUNK")):
                if preview is None:
                    preview = self._tools[key] = _ToolPreview("tool", time.monotonic())
                # Arguments are retained separately from the collapsed preview.
                # Coalesce chunks at END instead of copying growing JSON per token.
                if preview.parts is None:
                    preview.parts = []
                available = max(
                    0, (self._limit or self.transcript.max_bytes) - sum(item.size for item in self._tools.values())
                )
                if available and text:
                    preview.parts.append(text[:available])
                preview.size += min(len(text), available)
                preview.truncated |= len(text) > available
            elif event_type.endswith("RESULT"):
                name = preview.name if preview else "tool"
                if name.startswith("shell"):
                    command = preview.summary if preview and name in {"shell_exec", "shell_start"} else ""
                    self._observe_shell_result(text, command, run_id)
                if (not child or detailed) and (name != "ask_user_question" or detailed):
                    elapsed = f" · {time.monotonic() - preview.started:.1f}s" if preview else ""
                    result = tool_result(name, text)
                    state, _, output = result.partition("\n")
                    header = f"{name} · {state}{elapsed}" + (f" · {identity}" if child else "")
                    summary = preview.summary if preview else ""
                    brief = " · ".join(
                        item
                        for item in (header, " ".join(summary.split())[:100], output.split("\n", 1)[0][:100])
                        if item
                    )
                    shell_preview = None
                    if name.startswith("shell"):
                        shell_preview = shell_result_preview(text, summary, self.status.max_tool_result_lines)
                    if shell_preview is not None:
                        brief = f"{name} · {shell_preview}"
                    arguments = preview.arguments if preview else ""
                    body = header + "\n" + (f"Arguments · {label}\n{arguments}\n" if arguments else "") + result + "\n"
                    block_id = preview.block_id if preview is not None else None
                    if block_id is None or not self.transcript.replace(block_id, terminal_text(body)):
                        block_id = self.transcript.append(terminal_text(body), collapsed_lines=1, kind="tool")
                    self.transcript.preview(
                        block_id, terminal_text(brief), len(brief.splitlines()) if shell_preview is not None else 1
                    )
                    self.append(header + "\n", display=False)
                self._tools.pop(key, None)
                if not child and self.status.state != "cancelling":
                    self.status.state = "working"
                self.boundary = True
            elif event_type.endswith("END"):
                # END completes argument generation, not tool execution.
                if preview:
                    preview.arguments = "".join(preview.parts or ())
                    preview.parts = None
                    preview.summary = tool_preview(preview.arguments)
                    if preview.name in {"edit", "multi_edit", "summarize", "compact"}:
                        preview.arguments = ""
                    elif preview.arguments:
                        preview.arguments = tool_arguments(preview.name, preview.arguments)
                        if preview.truncated:
                            preview.arguments += "\n[Arguments exceed display budget; /history reads retained content]"
                    preview.size = len(preview.arguments)
                    if preview.block_id is not None and preview.arguments:
                        header = f"{preview.name} · running" + (f" · {identity}" if child else "")
                        self.transcript.replace(preview.block_id, terminal_text(header + "\n" + preview.arguments))
                        brief = header + " · " + preview.summary
                        self.transcript.preview(preview.block_id, terminal_text(brief), 1)
                self.boundary = True
            # START-only and malformed streams obey the same bound as ARGS.
            while len(self._tools) > 128:
                self._tools.pop(next(iter(self._tools)))
            return
        if event_type in {"RUN_FINISHED", "RUN_ERROR"}:
            self.end_process_observations(run_id)
        if event_type == "RUN_ERROR":
            self.finish()
            self.append(f"Error: {payload.get('message', payload.get('code', 'run failed'))}\n")
        elif event_type == "CUSTOM":
            value = payload.get("value")
            if isinstance(value, dict):
                event = value.get("event")
                if not isinstance(event, dict):
                    return
                name = payload.get("name")
                if name == "a13n.input.media":
                    media = event.get("content")
                    if isinstance(media, dict) and (not child or detailed):
                        label = str(media.get("media_type") or media.get("kind") or "media")
                        if isinstance(media.get("size_bytes"), int):
                            label += f" · {media['size_bytes']:,} bytes"
                        reference = media.get("url") or media.get("file_id")
                        if isinstance(reference, str):
                            label += f" · {reference}"
                        self.finish()
                        self.append(f"> [{label}]\n", kind="user")
                    return
                if name == "a13n.shell.status":
                    process_id, phase = event.get("process_id"), event.get("phase")
                    if isinstance(process_id, str) and isinstance(phase, str):
                        self._shell_observation(run_id, process_id).phase = phase
                    if not child or detailed:
                        self._shell_notification(event, run_id, identity if child else "")
                    # Recognized routine statuses are intentionally quiet, not
                    # unknown capability events to render as raw JSON.
                    return
                panel = capability_panel(name, event)
                if panel is not None:
                    if not child or detailed:
                        self.finish()
                        self.append(f"{panel.title}\n{panel.body}\n", kind=panel.kind)
                    return
                if event.get("event_kind") == "capability":
                    if not child or detailed:
                        self.finish()
                        self.append(f"[Event · {name}]\n")
                        self.append(
                            json.dumps(event, ensure_ascii=False, indent=2) + "\n",
                            collapsed_lines=self.status.max_tool_result_lines,
                        )
                    return
                mutation = event.get("payload")
                if isinstance(mutation, dict):
                    kind = str(mutation.get("type", ""))
                    if kind.startswith(("compaction_", "handoff_")):
                        self.finish()
                        title = "Compact" if kind.startswith("compaction_") else "Summary"
                        self.append(
                            f"[{title} · {identity}] {kind}\n"
                            + json.dumps(mutation, ensure_ascii=False, indent=2)
                            + "\n",
                            collapsed_lines=1,
                            kind="compact" if kind.startswith("compaction_") else "summary",
                        )
                    elif kind == "task_changed" and not child:
                        self.tasks.ingest(mutation)
                if name == "a13n.pydantic_ai.enqueued_messages" and event.get("event_kind") == "enqueued_messages":
                    # ModelInputEvent owns applied input. Acceptance is a local
                    # notification; queue/delivery facts must not echo it again.
                    return
                elif name == "a13n.pydantic_ai.function_tool_result":
                    self.finish()
                    self.append(
                        "[Tool · native result/retry]\n"
                        + json.dumps(event.get("part"), ensure_ascii=False, indent=2)
                        + "\n"
                    )
