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
        return CapabilityPanel(title, summary, "compact" if compact else "summary")
    if name == "a13n.filesystem.edit_applied":
        before, after, path = event.get("before"), event.get("after"), event.get("file_path")
        if not isinstance(before, str) or not isinstance(after, str) or not isinstance(path, str):
            return None
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


def tool_preview(arguments: str) -> str:
    try:
        value = json.loads(arguments)
    except ValueError:
        return arguments[:240]
    if isinstance(value, dict):
        for key in ("command", "file_path", "path", "query", "pattern", "subject", "process_id"):
            if isinstance(value.get(key), str):
                return value[key][:500]
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
        if name.startswith("shell") and isinstance(value.get("status"), dict):
            state = shell_outcome(value["status"]) or state
        text = json.dumps(value, ensure_ascii=False, indent=2)
    return f"{state}\n{text}"


def shell_result_preview(text: str, command: str, max_lines: int) -> str | None:
    """Read native Shell facts: a successful API call can still exit nonzero."""
    try:
        value = json.loads(text)
    except ValueError:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("status"), dict):
        return None
    status = value["status"]
    phase = status.get("phase")
    if not isinstance(phase, str):
        return None
    outcome = shell_outcome(status) or ("failed" if value.get("ok") is False else "")
    code = status.get("exit_code")
    state = [outcome] if outcome else []
    if isinstance(code, int) and not isinstance(code, bool):
        state.append(f"exit {code}")
    elif not state:
        state.append("running" if phase == "running" else "finished" if phase == "exited" else phase)
    # Keep status ahead of the command so narrow terminals and long commands
    # cannot hide failure or the exit code. Empty capture needs no body at all.
    title = " ".join(command.split())[:500] or "command unavailable"
    lines = [" · ".join((*state, title))]
    output_lines: list[str] = []
    more_output = False
    for stream in ("stderr", "stdout"):
        page = value.get(stream)
        if not isinstance(page, dict):
            continue
        coverage = page.get("coverage")
        if coverage in {"partial", "unknown"} or page.get("content_complete") is False:
            coverage = coverage if coverage in {"partial", "unknown"} else "incomplete"
            lines.append(f"[output {coverage} · {stream}]")
        omitted = page.get("omitted_before_bytes")
        if isinstance(omitted, int) and omitted > 0:
            lines.append(f"[{stream} · {omitted} earlier bytes omitted]")
        output = page.get("text")
        if isinstance(output, str) and output.strip():
            # Never parse redirected/merged stdout as stderr, Markdown, or a
            # structured tool result. Preserve literal content and indentation.
            parts = output.splitlines()
            limit = min(max_lines, 3)
            if stream == "stderr":
                output_lines.append("stderr:")
            output_lines.extend(parts[:limit])
            more_output |= len(parts) > limit
    if value.get("disclosure"):
        lines.append("[Output disclosure · Ctrl+O details]")
    if more_output:
        lines.append("… more output · Ctrl+O details")
    # Coverage and omission facts must survive the transcript preview budget,
    # even when a captured line is very long.
    return "\n".join((*lines, *output_lines))
