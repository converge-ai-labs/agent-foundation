"""Append-only bounded stream presentation; no growing transcript reparse."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass


def terminal_text(value: str) -> str:
    """Render untrusted content as text, never as terminal control sequences."""
    return "".join(char for char in value if char in "\n\t" or (ord(char) >= 32 and not 127 <= ord(char) <= 159))


@dataclass(slots=True)
class Status:
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

    def line(self) -> str:
        elapsed = time.monotonic() - self.started if self.started is not None else self.elapsed
        used = "?" if self.context_tokens is None else f"{self.context_tokens:,}"
        window = "?" if self.context_window is None else f"{self.context_window:,}"
        return terminal_text(
            f" {self.model} · {self.thinking} · context {used}/{window} · {elapsed:.0f}s · {self.state} · {self.mode} "
        )


class StreamRenderer:
    """Keep only a bounded pending batch and bounded per-tool argument tails.

    Consumers flush at most 25 times per second plus semantic boundaries. Old
    output lives in terminal scrollback, not an application transcript widget.
    """

    def __init__(self, status: Status, *, limit: int | None = None) -> None:
        self.status = status
        self._limit = limit
        self._pending: list[str] = []
        self._size = 0
        self._arguments: dict[str, str] = {}
        self.assistant_seen = False
        self.gap = False
        self.boundary = False
        self._line_open = False

    @property
    def limit(self) -> int:
        return self._limit or self.status.max_tool_argument_chars

    def append(self, text: str) -> None:
        safe = terminal_text(text)
        self._pending.append(safe)
        self._size += len(safe)
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
            self.append("\n")
        self.boundary = True

    def ingest(self, event_type: str, payload: Mapping[str, object] | None, *, child: bool = False) -> None:
        if payload is None:
            self.gap = True
            return
        detailed = self.status.mode == "detailed"
        delta = payload.get("delta") or payload.get("content") or ""
        text = delta if isinstance(delta, str) else json.dumps(delta, ensure_ascii=False)
        thinking = event_type.startswith(("REASONING_MESSAGE", "THINKING_TEXT_MESSAGE"))
        assistant = event_type.startswith("TEXT_MESSAGE")
        if thinking or assistant:
            if child and not detailed:
                return
            if thinking and not detailed:
                return
            if event_type.endswith("START"):
                if self._line_open:
                    self.append("\n")
                if child:
                    self.append("[Subagent] ")
                if thinking:
                    self.append("[Thinking] ")
            elif event_type.endswith("END"):
                if self._line_open:
                    self.append("\n")
                self.boundary = True
            else:
                self.append(text)
                if assistant and not child and text:
                    self.assistant_seen = True
            return
        if event_type.startswith("TOOL_CALL"):
            key = str(payload.get("tool_call_id", "unknown"))
            if event_type.endswith("START"):
                self.status.state = str(payload.get("tool_call_name", "tool"))[:60]
                if detailed:
                    self.finish()
                    self.append(f"[Tool] {self.status.state}\n")
                self._arguments[key] = ""
            elif event_type.endswith(("ARGS", "CHUNK")):
                # Keep names/calls bounded even if a producer omits end events.
                if len(self._arguments) > 128:
                    self._arguments.pop(next(iter(self._arguments)))
                self._arguments[key] = (self._arguments.get(key, "") + text)[: self.limit]
            elif event_type.endswith("RESULT"):
                if detailed:
                    lines = text.splitlines()
                    self.append("\n".join(lines[: self.status.max_tool_result_lines])[: self.limit] + "\n")
                    if len(lines) > self.status.max_tool_result_lines or len(text) > self.limit:
                        self.append("[Result truncated; /history shows retained details]\n")
                self._arguments.pop(key, None)
                self.boundary = True
            elif event_type.endswith("END"):
                arguments = self._arguments.pop(key, "")
                if detailed and arguments:
                    self.append(arguments + (" [arguments truncated]" if len(arguments) == self.limit else "") + "\n")
                self.boundary = True
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
