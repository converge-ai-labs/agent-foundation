"""Correlate native context lifecycle and summary content without inventing completion."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from .transcript import Transcript, bounded_text


@dataclass(slots=True)
class ContextActivity:
    title: str
    block_id: int
    details_id: int
    summary: str = ""
    phase: Literal["active", "completed", "failed"] = "active"
    error: str = ""
    observations: dict[str, str] = field(default_factory=dict)

    def update(self, transcript: Transcript, kind: str, event: dict[str, object]) -> None:
        summary = event.get("summary")
        if isinstance(summary, str):
            files = event.get("files")
            if isinstance(files, list):
                paths = [path for path in files if isinstance(path, str)]
                if paths:
                    summary += "\n\nFiles to inspect:\n" + "\n".join(paths)
            self.summary = bounded_text(summary, transcript.block_bytes)
        else:
            self.observations[kind] = json.dumps(event, ensure_ascii=False, indent=2)
            transcript.replace(self.details_id, "Context lifecycle\n" + "\n".join(self.observations.values()))
            if self.phase == "active":
                if kind.endswith("_completed"):
                    self.phase = "completed"
                elif kind.endswith("_failed"):
                    self.phase = "failed"
                    self.error = str(event.get("error_code", "operation failed"))
        if self.phase == "completed":
            source = self.title + "\n" + (self.summary or "Summary content unavailable.")
            block_kind = "compact" if self.title == "Compact" else "summary"
        elif self.phase == "failed":
            source = f"{self.title} failed: {self.error}"
            block_kind = "tool"
        else:
            source = "Compacting context…" if self.title == "Compact" else "Summarizing context…"
            block_kind = "tool"
        transcript.replace(self.block_id, source, kind=block_kind)
