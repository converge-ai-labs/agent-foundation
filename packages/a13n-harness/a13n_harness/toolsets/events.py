"""Content-bearing native observations, separate from content-free telemetry."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai.messages import CapabilityEvent


@dataclass(kw_only=True)
class FileEditAppliedEvent(CapabilityEvent, namespace="a13n.filesystem", name="edit_applied"):
    """Actual text before and after one successful edit or atomic multi-edit."""

    file_path: str
    before: str
    after: str


@dataclass(kw_only=True)
class HandoffSummaryEvent(CapabilityEvent, namespace="a13n.context", name="handoff_summary"):
    """Persisted summary awaiting application at the next model boundary."""

    operation_id: str
    summary: str
    files: tuple[str, ...]


@dataclass(kw_only=True)
class ShellStatusEvent(CapabilityEvent, namespace="a13n.shell", name="status"):
    """Observed process status; a callback is advisory, not execution authority."""

    process_id: str | None
    phase: str
    exit_code: int | None
    callback: bool = False
