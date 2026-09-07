"""Generic tool payload previews; authoritative specialty content comes from events."""

from __future__ import annotations

import difflib
import json
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CapabilityPanel:
    title: str
    body: str
    kind: str


def capability_panel(name: object, event: Mapping[str, object]) -> CapabilityPanel | None:
    """Interpret known native facts here, never in the shared stream protocol."""
    if name in {"a13n.context.compaction_summary", "a13n.context.handoff_summary"}:
        summary = event.get("summary")
        if not isinstance(summary, str):
            return None
        compact = name == "a13n.context.compaction_summary"
        title = "Compact summary" if compact else "Summary · prepared"
        files = event.get("files")
        if isinstance(files, list) and files:
            summary += "\n\nFiles to inspect:\n" + "\n".join(str(path) for path in files)
        return CapabilityPanel(f"{title} · {event.get('operation_id')}", summary, "compact" if compact else "summary")
    if name == "a13n.filesystem.edit_applied":
        before, after, path = event.get("before"), event.get("after"), event.get("file_path")
        if not isinstance(before, str) or not isinstance(after, str) or not isinstance(path, str):
            return None
        if len(before) + len(after) > 128 * 1024 or before.count("\n") + after.count("\n") > 2000:
            return CapabilityPanel(
                f"Edit · applied · {path} · {event.get('tool_call_id')}",
                "Unified diff omitted: comparison budget exceeded. Actual before/after text follows.\n"
                + f"--- {path} (before)\n{before}\n+++ {path} (after)\n{after}",
                "edit",
            )
        lines = difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=path, tofile=path
        )
        body = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)
        return CapabilityPanel(
            f"Edit · applied · {path} · {event.get('tool_call_id')}", body or "Empty file created.\n", "edit"
        )
    if name == "a13n.shell.status":
        callback = " · completion notification" if event.get("callback") else ""
        code = event.get("exit_code")
        return CapabilityPanel(
            f"Shell · {event.get('process_id') or event.get('tool_call_id') or 'foreground'} · {event.get('phase')}{callback}",
            f"Exit code: {code}" if code is not None else "",
            "shell",
        )
    return None


def tool_arguments(name: str, arguments: str) -> str:
    try:
        value = json.loads(arguments)
    except (ValueError, TypeError):
        return arguments
    if not isinstance(value, dict):
        return arguments
    return json.dumps(value, ensure_ascii=False, indent=2)


def tool_result(name: str, text: str) -> str:
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        value = None
    state = "result"
    if isinstance(value, dict):
        if value.get("ok") is False:
            state = "failed · no edit confirmed" if name in {"edit", "multi_edit"} else "failed"
        elif value.get("ok") is True and name in {"edit", "multi_edit"}:
            state = "completed"
        text = json.dumps(value, ensure_ascii=False, indent=2)
    return f"[{name} · {state}]\n{text}"
