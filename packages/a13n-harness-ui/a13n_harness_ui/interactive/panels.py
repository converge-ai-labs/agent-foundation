"""Generic tool payload previews; authoritative specialty content comes from events."""

from __future__ import annotations

import difflib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePath


@dataclass(frozen=True, slots=True)
class CapabilityPanel:
    title: str
    body: str
    kind: str


def display_path(path: str, directory: PurePath | None) -> str:
    """Shorten lexical descendants only, without resolving files or consulting cwd.

    Keep parent traversal untouched: collapsing it could change meaning across
    symlinks. Outside paths and paths from another filesystem remain verbatim.
    """
    if directory is None or not directory.is_absolute():
        return path
    candidate = type(directory)(path)
    if not candidate.is_absolute() or ".." in candidate.parts or ".." in directory.parts:
        return path
    try:
        return str(candidate.relative_to(directory))
    except ValueError:
        return path


def capability_panel(
    name: object, event: Mapping[str, object], *, directory: PurePath | None = None
) -> CapabilityPanel | None:
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
        return CapabilityPanel(title, summary, "compact" if compact else "summary")
    if name == "a13n.filesystem.edit_applied":
        before, after, path = event.get("before"), event.get("after"), event.get("file_path")
        if not isinstance(before, str) or not isinstance(after, str) or not isinstance(path, str):
            return None
        path = display_path(path, directory)
        if len(before) + len(after) > 128 * 1024 or before.count("\n") + after.count("\n") > 2000:
            return CapabilityPanel(
                f"Edit · {path} · applied",
                "Unified diff omitted: comparison budget exceeded. Actual before/after text follows.\n"
                + f"Before:\n{before}\nAfter:\n{after}",
                "edit",
            )
        lines = difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=path, tofile=path
        )
        # The panel title owns the path and counts. Skip only the two generated
        # file headers, never content lines that happen to start with +++ or ---.
        next(lines, None)
        next(lines, None)
        parts: list[str] = []
        added = removed = 0
        for line in lines:
            if line.startswith("@@"):
                if parts:
                    parts.append("…\n")
                continue
            added += line.startswith("+")
            removed += line.startswith("-")
            parts.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
        body = "".join(parts)
        return CapabilityPanel(f"Edit · {path} · +{added} -{removed}", body or "Empty file created.\n", "edit")
    return None


def tool_arguments(name: str, arguments: str) -> str:
    try:
        value = json.loads(arguments)
    except (ValueError, TypeError):
        return arguments
    if not isinstance(value, dict):
        return arguments
    return json.dumps(value, ensure_ascii=False, indent=2)


def tool_preview(arguments: str, *, name: str = "", directory: PurePath | None = None) -> str:
    try:
        value = json.loads(arguments)
    except ValueError:
        return arguments[:240]
    if isinstance(value, dict):
        if name in {"note_write", "note_get", "note_delete"}:
            key = value.get("key")
            return " ".join(key.split())[:500] if isinstance(key, str) else "all notes" if name == "note_get" else ""
        for key in ("command", "file_path", "path", "query", "pattern", "subject", "process_id"):
            if isinstance(value.get(key), str):
                summary = value[key]
                if key in {"file_path", "path"} and name in {"view", "write", "edit", "multi_edit", "ls"}:
                    summary = display_path(summary, directory)
                return summary[:500]
        return ", ".join(value)[:160]
    return str(value)[:160]


def shell_outcome(status: Mapping[str, object]) -> str:
    """Only highlight noteworthy outcomes; routine process phases stay in details."""
    phase = status.get("phase")
    labels = {
        "timed_out": "timed out",
        "cancelled": "cancelled",
        "signaled": "interrupted",
        "failed": "failed",
        "unknown": "status unavailable",
        "missing": "process unavailable",
    }
    if isinstance(phase, str) and phase in labels:
        return labels[phase]
    code = status.get("exit_code")
    if isinstance(code, int) and code != 0:
        return "failed"
    return ""


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
        elif value.get("ok") is True and name in {"note_write", "note_get", "note_delete"}:
            action = value.get("action")
            state = (
                action.replace("_", " ")
                if isinstance(action, str) and action in {"created", "updated", "deleted", "already_absent"}
                else "completed"
            )
        if name.startswith("shell") and isinstance(value.get("status"), dict):
            state = shell_outcome(value["status"]) or state
        text = json.dumps(value, ensure_ascii=False, indent=2)
    return f"{state}\n{text}"


def shell_result_preview(text: str, command: str) -> str:
    """Summarize native status only; captured output belongs in expanded details."""
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    if not isinstance(value, dict):
        value = {}
    status = value.get("status")
    if not isinstance(status, dict):
        status = {}
    phase = status.get("phase")
    outcome = shell_outcome(status)
    state = ["failed"] if value.get("ok") is False else []
    if outcome and outcome not in state:
        state.append(outcome)
    code = status.get("exit_code")
    if isinstance(code, int) and not isinstance(code, bool):
        state.append(f"exit {code}")
    elif not state:
        state.append("running" if phase == "running" else "finished" if phase == "exited" else "status unavailable")
    # Inline diagnostics precede the command and are deduplicated across streams.
    # No output text is inspected, interpreted, or copied into the concise row.
    for stream in ("stderr", "stdout"):
        page = value.get(stream)
        if not isinstance(page, dict):
            continue
        coverage = page.get("coverage")
        if coverage in {"partial", "unknown"} or page.get("content_complete") is False:
            coverage = coverage if coverage in {"partial", "unknown"} else "incomplete"
            label = f"output {coverage}"
            if label not in state:
                state.append(label)
        omitted = page.get("omitted_before_bytes")
        if isinstance(omitted, int) and omitted > 0 and "output omitted" not in state:
            state.append("output omitted")
    if value.get("disclosure"):
        state.append("output disclosure")
    title = " ".join(command.split())[:500] or "command unavailable"
    return " · ".join((*state, title))
