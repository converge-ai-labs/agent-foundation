"""Semantic stream presentation with bounded per-message display state."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from a13n_stream_protocol import ContentMetadata, CustomEventAssembler

from .local_shell import LocalShellEvent
from .panels import capability_panel, tool_arguments, tool_result
from .transcript import Transcript


def terminal_text(value: str) -> str:
    """Render untrusted content as text, never as terminal control sequences."""
    return "".join(char for char in value if char in "\n\t" or (ord(char) >= 32 and not 127 <= ord(char) <= 159))


@dataclass(slots=True)
class Status:
    theme: Literal["auto", "dark", "light"] = "auto"
    theme_explicit: bool = False
    state: str = "starting"
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
    update_notice: str | None = None

    def line(self, width: int | None = None) -> str:
        elapsed = time.monotonic() - self.started if self.started is not None else self.elapsed
        context = (
            "--"
            if self.context_tokens is None or not self.context_window
            else f"{100 * self.context_tokens / self.context_window:.0f}%"
        )
        fields = [
            self.state.capitalize(),
            self.model.split(":")[-1],
            self.thinking,
            f"ctx {context}",
            f"{elapsed:.0f}s",
        ]
        while width is not None and len(" · ".join(fields)) + 2 > width and len(fields) > 2:
            fields.pop(2 if len(fields) == 5 else 1)
        return terminal_text(" " + " · ".join(fields) + " ")


@dataclass(slots=True)
class _ToolPreview:
    name: str
    started: float
    arguments: str = ""
    parts: list[str] | None = None
    size: int = 0
    truncated: bool = False


class StreamRenderer:
    """Keep only a bounded pending batch and bounded per-tool argument tails.

    The App owns durable history. This adapter retains bounded semantic blocks
    and a bounded drain buffer for observation/testing, not execution authority.
    """

    def __init__(self, status: Status, *, limit: int | None = None) -> None:
        self.status = status
        self.transcript = Transcript()
        self._messages: dict[tuple[str, str, str], int] = {}
        self._limit = limit
        self._pending: list[str] = []
        self._size = 0
        self._tools: dict[tuple[str, str], _ToolPreview] = {}
        self._steering: dict[tuple[str, str], str] = {}
        self.assistant_seen = False
        self.gap = False
        self.boundary = False
        self._line_open = False
        self._local_output: dict[str, int] = {}
        self._custom_events = CustomEventAssembler()

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
                    label = (f"**Subagent · {identity}**\n\n" if child else "") + (
                        "**Thinking**\n\n" if thinking else "> " if user else ""
                    )
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
                if not child or detailed:
                    self.finish()
                    self.append(f"[Tool] {preview.name} · {label}\n")
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
                if not child or detailed:
                    elapsed = f" · {time.monotonic() - preview.started:.1f}s" if preview else ""
                    self.append(f"[Result] {name} · {label}{elapsed}\n")
                    self.append(
                        tool_result(name, text) + "\n",
                        collapsed_lines=self.status.max_tool_result_lines,
                    )
                self._tools.pop(key, None)
                if not child and self.status.state != "cancelling":
                    self.status.state = "working"
                self.boundary = True
            elif event_type.endswith("END"):
                # END completes argument generation, not tool execution.
                if preview:
                    preview.arguments = "".join(preview.parts or ())
                    preview.parts = None
                    preview.size = 0
                    if (not child or detailed) and preview.arguments:
                        self.append(
                            tool_arguments(preview.name, preview.arguments)
                            + (
                                "\n[Arguments exceed display budget; /history reads retained content]"
                                if preview.truncated
                                else ""
                            )
                            + "\n",
                            collapsed_chars=self.status.max_tool_argument_chars,
                            collapsed_lines=self.status.max_tool_result_lines,
                        )
                    preview.arguments = ""
                self.boundary = True
            # START-only and malformed streams obey the same bound as ARGS.
            while len(self._tools) > 128:
                self._tools.pop(next(iter(self._tools)))
            return
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
                panel = capability_panel(name, event)
                if panel is not None:
                    if not child or detailed:
                        self.finish()
                        self.append(f"[{panel.title}]\n", kind=panel.kind)
                        self.append(panel.body + "\n", kind=panel.kind, markdown=panel.kind in {"summary", "compact"})
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
                            + "\n"
                        )
                    elif kind == "steering_input_enqueued":
                        source = str(mutation.get("source", "unknown"))
                        self._steering[(run_id, str(mutation.get("enqueue_id", "unknown")))] = source
                        while len(self._steering) > 256:
                            self._steering.pop(next(iter(self._steering)))
                        title = "Steer" if source == "external" else "Input"
                        self.append(
                            f"[{title} · queued · {identity}]\n"
                            + json.dumps(mutation, ensure_ascii=False, indent=2)
                            + "\n"
                        )
                if name == "a13n.pydantic_ai.enqueued_messages" and event.get("event_kind") == "enqueued_messages":
                    self.finish()
                    enqueue_id = str(event.get("enqueue_id", "unknown"))
                    source = self._steering.pop((run_id, enqueue_id), None)
                    title = "Steer" if source == "external" else "Input"
                    self.append(
                        f"[{title} · delivered · {identity} · {enqueue_id}]\nAdditional input delivered at a model boundary.\n"
                    )
                elif name == "a13n.pydantic_ai.function_tool_result":
                    self.finish()
                    self.append(
                        "[Tool · native result/retry]\n"
                        + json.dumps(event.get("part"), ensure_ascii=False, indent=2)
                        + "\n"
                    )
