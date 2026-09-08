"""Terminal recovery guidance over the shared private exception reporter."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from a13n_harness_ui.diagnostics import exception_feedback

from .lifecycle import resume_hint

if TYPE_CHECKING:
    from a13n_harness_ui.cli import CliRequest


def exception_report(
    error: BaseException,
    *,
    session_id: str | None,
    phase: str,
    request: CliRequest,
    directory: Path,
    loop_context: Mapping[str, object] | None = None,
) -> str:
    feedback = exception_feedback(error, thread_id=session_id, phase=phase, loop_context=loop_context)
    recovery = (
        f"{resume_hint(request, session_id, directory)}\nResume uses the last successfully saved checkpoint."
        if session_id
        else "No saved session is known. Unsent input is not in the report."
    )
    return f"Unexpected {type(error).__name__}.\n{feedback}\n{recovery}"


def pending_task_warning(error: BaseException, *, session_id: str | None, context: Mapping[str, object]) -> str:
    feedback = exception_feedback(error, thread_id=session_id, phase="terminal event loop", loop_context=context)
    return (
        "Warning: a background task was destroyed before it finished. The terminal remains open, "
        "but the lost task was not recovered and work may be incomplete. "
        "Check /status; use /cancel if active work stops progressing. No work was retried.\n"
        f"{feedback}"
    )
