"""Concise semantic tool rows and bounded exploration groups; no execution authority."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .panels import display_path
from .transcript import Transcript

EXPLORATION_TOOLS = {"view", "glob", "grep", "ls"}
CONTEXT_TOOLS = {"summarize", "compact"}


def arguments_object(text: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def semantic_tool_row(name: str, arguments: str, directory: Path | None = None) -> tuple[str, str | None]:
    value = arguments_object(arguments)

    def string(key: str, default: str = "") -> str:
        item = value.get(key)
        return " ".join(item.split())[:500] if isinstance(item, str) else default

    if name in {"view", "ls"}:
        raw = value.get("file_path" if name == "view" else "path")
        path = raw if isinstance(raw, str) else "path unavailable" if name == "view" else "."
        shown = " ".join(display_path(path, directory).split())[:500]
        return f"{'Read' if name == 'view' else 'List'} {shown}", raw if name == "view" and isinstance(
            raw, str
        ) else None
    if name in {"glob", "grep"}:
        return f"{'Find' if name == 'glob' else 'Search'} {string('pattern')} in {string('root', '.')}", None
    if name.startswith("shell"):
        return f"Run {string('command', 'command unavailable')}", None
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


def failure_reason(text: str, state: str) -> str:
    """Prefer an explicit tool diagnostic, never an arbitrary successful output line."""
    value = arguments_object(text)
    error = value.get("error")
    if isinstance(error, dict):
        details = error.get("details")
        if isinstance(details, dict):
            reason = details.get("hint") or details.get("reason")
            if isinstance(reason, str):
                field_name = details.get("field")
                return (f"{field_name}: " if isinstance(field_name, str) else "") + " ".join(reason.split())[:240]
        code = error.get("code")
        if isinstance(code, str):
            return code
    if state == "retry":
        return "correct the arguments and retry"
    return "denied" if state == "denied" else "operation failed; Ctrl+O details"


@dataclass(slots=True)
class ExplorationMember:
    block_id: int
    brief: str
    read_path: str | None = None
    active: bool = True
    failed: bool = False


@dataclass(slots=True)
class ExplorationGroup:
    identity: tuple[str, str | None]
    members: list[ExplorationMember] = field(default_factory=list)

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
