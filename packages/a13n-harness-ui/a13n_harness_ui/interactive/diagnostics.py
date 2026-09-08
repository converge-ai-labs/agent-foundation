"""Terminal recovery guidance over the shared private exception reporter."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from a13n_harness_ui.diagnostics import exception_feedback

from .lifecycle import resume_hint

if TYPE_CHECKING:
    from a13n_harness_ui.cli import CliRequest


def exception_report(
    error: BaseException, *, session_id: str | None, phase: str, request: CliRequest, directory: Path
) -> str:
    feedback = exception_feedback(error, thread_id=session_id, phase=phase)
    recovery = (
        f"{resume_hint(request, session_id, directory)}\nResume uses the last successfully saved checkpoint."
        if session_id
        else "No saved session is known. Unsent input is not in the report."
    )
    return f"Unexpected {type(error).__name__}.\n{feedback}\n{recovery}"
