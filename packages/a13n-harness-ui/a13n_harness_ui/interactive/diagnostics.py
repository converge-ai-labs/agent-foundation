"""Local-only exception reports, never execution or credential snapshots."""

from __future__ import annotations

import json
import os
import platform
import tempfile
import traceback
from collections.abc import Iterator
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING

from .lifecycle import resume_hint

if TYPE_CHECKING:
    from a13n_harness_ui.cli import CliRequest


def exception_report(
    error: BaseException, *, session_id: str | None, phase: str, request: CliRequest, directory: Path
) -> str:
    """Keep exception chains and frame locations without locals or source lines.

    Exception messages can themselves contain sensitive values. The report is
    private and temporary; users must review it before attaching it to an issue.
    Failure to save diagnostics must not replace the original failure.
    """
    try:
        release = version("a13n-harness-ui")
    except PackageNotFoundError:
        release = "development"
    trace = traceback.TracebackException.from_exception(error, capture_locals=False)
    report = {
        "version": release,
        "python": platform.python_version(),
        "platform": platform.system(),
        "phase": phase,
        "session_id": session_id,
        "exceptions": [
            {
                "error": "".join(item.format_exception_only()),
                "frames": [
                    {"file": frame.filename, "line": frame.lineno, "function": frame.name} for frame in item.stack
                ],
            }
            for item in _exception_chain(trace)
        ],
        "recovery": "Only already selected continuations are resumable. In-flight changes and unsent drafts are not saved by this report.",
    }
    try:
        descriptor, filename = tempfile.mkstemp(prefix="a13n-harness-ui-error-", suffix=".json")
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        location = f"Diagnostic report: {Path(filename)}. Review for sensitive content before attaching it to an issue. Nothing was uploaded."
    except OSError:
        location = "The diagnostic report could not be saved."
    recovery = (
        f"{resume_hint(request, session_id, directory)}\nIn-flight work may be lost."
        if session_id
        else "No saved session is known. Unsent input is not in the report."
    )
    return f"Unexpected {type(error).__name__}: {error or '(no message)'}\n{location}\n{recovery}"


def _exception_chain(trace: traceback.TracebackException) -> Iterator[traceback.TracebackException]:
    yield trace
    if trace.__cause__ is not None:
        yield from _exception_chain(trace.__cause__)
    elif trace.__context__ is not None:
        yield from _exception_chain(trace.__context__)
    for child in trace.exceptions or ():
        yield from _exception_chain(child)
