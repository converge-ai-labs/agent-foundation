"""Semantic stream presentation with bounded per-message display state."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

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

    def line(self, width: int | None = None) -> str:
        elapsed = time.monotonic() - self.started if self.started is not None else self.elapsed
        used = "?" if self.context_tokens is None else f"{self.context_tokens:,}"
        window = "?" if self.context_window is None else f"{self.context_window:,}"
        if width is not None and width < 100:
            model = self.model.split(":")[-1]
            return terminal_text(f" {self.state} · {elapsed:.0f}s · {used}/{window} · {model} · {self.thinking}")
        return terminal_text(
            f" {self.state} · {self.model} · {self.thinking} · context {used}/{window} · {elapsed:.0f}s · {self.environment} · {self.mode} "
        )


@dataclass(slots=True)
class _ToolPreview:
    name: str
    started: float
    arguments: str = ""
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
        self.assistant_seen = False
        self.gap = False
        self.boundary = False
        self._line_open = False

    @property
    def limit(self) -> int:
        return self._limit or self.status.max_tool_argument_chars

    def append(self, text: str, *, display: bool = True, markdown: bool = False) -> None:
        safe = terminal_text(text)
        if display and safe:
            self.transcript.append(safe, markdown=markdown)
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
        detailed = self.status.mode == "detailed"
        delta = payload.get("delta") or payload.get("content") or ""
        text = delta if isinstance(delta, str) else json.dumps(delta, ensure_ascii=False)
        thinking = event_type.startswith(("REASONING_MESSAGE", "THINKING_TEXT_MESSAGE"))
        assistant = event_type.startswith("TEXT_MESSAGE")
        identity = terminal_text(execution_id or run_id)
        if thinking or assistant:
            if not child and self.status.state != "cancelling":
                self.status.state = "thinking" if thinking else "responding"
            if (child or thinking) and not detailed:
                return
            key = (run_id, str(payload.get("message_id", "default")), "thinking" if thinking else "assistant")
            if event_type.endswith("START"):
                self.finish()
                self._messages.pop(key, None)
            elif event_type.endswith("END"):
                self._messages.pop(key, None)
                self.finish()
            elif text:
                block_id = self._messages.get(key)
                if block_id is None or not self.transcript.extend(block_id, terminal_text(text)):
                    label = (f"**Subagent · {identity}**\n\n" if child else "") + (
                        "**Thinking**\n\n" if thinking else ""
                    )
                    self._messages[key] = self.transcript.append(label + terminal_text(text), markdown=True)
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
                if detailed:
                    self.finish()
                    self.append(f"[Tool] {preview.name} · {label}\n")
            elif event_type.endswith(("ARGS", "CHUNK")):
                if preview is None:
                    preview = self._tools[key] = _ToolPreview("tool", time.monotonic())
                preview.truncated |= len(preview.arguments) + len(text) > self.limit
                preview.arguments = (preview.arguments + text)[: self.limit]
            elif event_type.endswith("RESULT"):
                if detailed:
                    name = preview.name if preview else "tool"
                    elapsed = f" · {time.monotonic() - preview.started:.1f}s" if preview else ""
                    self.append(f"[Result] {name} · {label}{elapsed}\n")
                    lines = text.splitlines()
                    self.append("\n".join(lines[: self.status.max_tool_result_lines])[: self.limit] + "\n")
                    if len(lines) > self.status.max_tool_result_lines or len(text) > self.limit:
                        self.append("[Result truncated; /history shows retained details]\n")
                self._tools.pop(key, None)
                if not child and self.status.state != "cancelling":
                    self.status.state = "working"
                self.boundary = True
            elif event_type.endswith("END"):
                # AG-UI END completes argument generation, not tool execution.
                if detailed and preview and preview.arguments:
                    self.append(preview.arguments + (" [arguments truncated]" if preview.truncated else "") + "\n")
                if preview:
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
                mutation = event.get("payload") if isinstance(event, dict) else None
                if isinstance(mutation, dict) and str(mutation.get("type", "")).startswith("compaction_"):
                    self.finish()
                    self.append(f"[Context] {mutation['type']}\n")
