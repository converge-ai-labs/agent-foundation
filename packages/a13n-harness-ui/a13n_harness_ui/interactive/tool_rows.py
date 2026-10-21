"""Concise semantic tool rows and bounded activity groups; no execution authority."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path
from typing import Literal

from .panels import display_path
from .transcript import Transcript

EXPLORATION_TOOLS = {"view", "glob", "grep", "ls"}
CONTEXT_TOOLS = {"summarize", "compact"}
EXECUTION_TOOLS = {"tool_execution_info", "tool_execution_wait", "tool_execution_result", "tool_execution_cancel"}


def activity_kind(name: str) -> Literal["explore", "executions"] | None:
    if name in EXPLORATION_TOOLS:
        return "explore"
    if name in EXECUTION_TOOLS:
        return "executions"
    return None


def arguments_object(text: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _excerpt(value: object, default: str = "") -> str:
    text = " ".join(value.split()) if isinstance(value, str) else ""
    return (text[:499] + "…" if len(text) > 500 else text) or default


def semantic_tool_row(name: str, arguments: str, directory: Path | None = None) -> tuple[str, str | None]:
    """Build plain preview text; the renderer sanitizes it with terminal_text."""
    value = arguments_object(arguments)

    if name in {"delegate", "steer_subagent"}:
        if name == "delegate":
            heading = "Delegate to " + _excerpt(value.get("subagent_name"), "subagent unavailable")
            instruction = _excerpt(value.get("prompt"))
        else:
            heading = "Steer " + _excerpt(value.get("execution_id"), "target unavailable")
            instruction = _excerpt(value.get("message"))
        return heading + (f"\n  {instruction}" if instruction else ""), None

    def string(key: str, default: str = "") -> str:
        return _excerpt(value.get(key), default)

    if name in EXECUTION_TOOLS:
        label = {
            "tool_execution_info": "Inspect execution" if string("execution_id") else "List tool executions",
            "tool_execution_wait": "Wait for execution",
            "tool_execution_result": "Retrieve result",
            "tool_execution_cancel": "Request cancellation",
        }[name]
        return label + (f" · {string('execution_id')}" if string("execution_id") else ""), None
    if name in {"view", "ls"}:
        raw = value.get("file_path" if name == "view" else "path")
        path = raw if isinstance(raw, str) else "path unavailable" if name == "view" else "."
        shown = _excerpt(display_path(path, directory))
        return f"{'Read' if name == 'view' else 'List'} {shown}", raw if name == "view" and isinstance(
            raw, str
        ) else None
    if name in {"glob", "grep"}:
        return f"{'Find' if name == 'glob' else 'Search'} {string('pattern')} in {string('root', '.')}", None
    if name in {"shell_exec", "shell_start", "shell_wait"}:
        return f"Run {string('command', 'command unavailable')}", None
    if name == "shell_info":
        return ("Inspect process" if string("process_id") else "List processes"), None
    if name == "shell_input":
        return "Send process input", None
    if name == "shell_signal":
        return "Signal process" + (f" · {string('signal')}" if string("signal") else ""), None
    raw_paths = value.get("paths")
    if name == "mkdir" and isinstance(raw_paths, list):
        paths = [path for path in raw_paths if isinstance(path, str)]
        targets = ", ".join(" ".join(display_path(path, directory).split()) for path in paths[:3])
        if len(paths) > 3:
            targets += f" (+{len(paths) - 3} more)"
        return "Call mkdir" + (f" {targets}" if targets else ""), None
    target = string("file_path") or string("key")
    if name == "note_get" and not target:
        target = "all notes"
    return f"Call {name}" + (f" {display_path(target, directory)}" if target else ""), None


def subagent_result_row(name: str, semantic: str, result: str) -> str | None:
    """Enrich a call preview with observed return facts, not live child state.

    None leaves the original semantic row unchanged. Native failed/denied/retry
    handling takes precedence in the caller, which also applies terminal_text.
    """
    if name not in {"delegate", "steer_subagent"}:
        return None
    value = arguments_object(result)
    if "part_kind" in value:
        if value.get("part_kind") != "tool-return" or value.get("outcome", "success") != "success":
            return None
        content = value.get("content")
        value = content if isinstance(content, dict) else arguments_object(content) if isinstance(content, str) else {}
    if value.get("ok") is False or "error" in value:
        return None
    execution_id = _excerpt(value.get("execution_id"))
    if not execution_id:
        return None
    if name == "delegate":
        status = value.get("status")
        if status is None:
            # Inline delegation can return an identity and output without status.
            detail = execution_id
        elif isinstance(status, str) and status in {"running", "succeeded", "failed", "cancelled", "lost"}:
            detail = f"{execution_id} · {status} at return"
        else:
            return None
    else:
        accepted = value.get("accepted")
        if not isinstance(accepted, bool) or semantic.partition("\n")[0] != f"Steer {execution_id}":
            return None
        # Admission to the guidance queue says nothing about child consumption.
        detail = "accepted for delivery" if accepted else "not accepted"
    heading, separator, instruction = semantic.partition("\n")
    return heading + f" · {detail}" + separator + instruction


def execution_result_row(name: str, semantic: str, result: str) -> str | None:
    """Describe recorded control results without implying ongoing observation."""
    if name not in EXECUTION_TOOLS:
        return None
    value = arguments_object(result)
    if value.get("part_kind") == "tool-return":
        content = value.get("content")
        value = content if isinstance(content, dict) else arguments_object(content) if isinstance(content, str) else {}
    if value.get("status") == "unavailable":
        return semantic + " · unavailable"
    if not all(isinstance(value.get(key), str) for key in ("execution_id", "tool_name", "run_id")):
        return None
    status = value.get("status")
    if not isinstance(status, str) or status not in {"queued", "running", "succeeded", "failed", "cancelled", "lost"}:
        return None
    observed = (
        "cancellation requested"
        if name == "tool_execution_cancel" and status in {"queued", "running"}
        else f"{status} at last check"
    )
    return semantic + " · " + observed


def failure_reason(text: str, state: str) -> str:
    """Prefer an explicit tool diagnostic, never an arbitrary successful output line."""
    value = arguments_object(text)
    error = value.get("error")
    if isinstance(error, dict):
        details = error.get("details")
        field_name = details.get("field") if isinstance(details, dict) else None
        prefix = _excerpt(field_name) + ": " if isinstance(field_name, str) and field_name.strip() else ""
        candidates = (
            details.get("hint") if isinstance(details, dict) else None,
            error.get("message"),
            details.get("reason") if isinstance(details, dict) else None,
        )
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return (prefix + _excerpt(candidate))[:240]
        code = error.get("code")
        if isinstance(code, str):
            return code
    if state == "retry":
        return "correct the arguments and retry"
    return "denied" if state == "denied" else "operation failed; Ctrl+O details"


@dataclass(slots=True)
class ToolActivityMember:
    block_id: int
    brief: str
    read_path: str | None = None
    active: bool = True
    failed: bool = False


@dataclass(slots=True)
class ToolActivityGroup:
    identity: tuple[str, str | None]
    kind: Literal["explore", "executions"] = "explore"
    members: list[ToolActivityMember] = field(default_factory=list)

    def refresh(self, transcript: Transcript) -> None:
        self.members[:] = [member for member in self.members if member.block_id in transcript.blocks]
        if not self.members:
            return
        first = self.members[0]
        for member in self.members:
            transcript.blocks[member.block_id].concise_anchor = None if member is first else first.block_id
        if len(self.members) == 1:
            transcript.preview(first.block_id, first.brief)
            return
        if self.kind == "executions":
            lines = [f"Tool executions · {len(self.members)} operations"]
            for brief, repeated in groupby(self.members, key=lambda member: member.brief):
                members = list(repeated)
                if len(members) > 1 and all(not member.failed and not member.active for member in members):
                    lines.append(f"  {brief} ({len(members)} checks)")
                else:
                    lines.extend("  " + member.brief for member in members)
            transcript.preview(first.block_id, "\n".join(lines), len(lines), limit=16384)
            return
        lines = ["Exploring" if any(member.active for member in self.members) else "Explored"]
        reads: set[str] = set()
        for member in self.members:
            if member.read_path is not None and not member.failed:
                if member.read_path in reads:
                    continue
                reads.add(member.read_path)
            else:
                reads.clear()
            lines.append("  " + member.brief)
        transcript.preview(first.block_id, "\n".join(lines), len(lines), limit=16384)
