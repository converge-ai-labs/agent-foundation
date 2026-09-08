"""Private local exception reports and opt-in open-source issue feedback."""

from __future__ import annotations

import json
import os
import platform
import tempfile
import traceback
from collections.abc import Iterator
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ISSUE_URL = "https://github.com/converge-ai-labs/agent-foundation/issues/new"


def exception_feedback(
    error: BaseException,
    *,
    thread_id: str | None,
    run_id: str | None = None,
    phase: str,
) -> str:
    """Write a private dump, excluding locals, source lines, and conversation state.

    Exception messages may contain sensitive provider content. They belong only
    in this user-reviewed temporary report, not normal logs or UI projections.
    Failure to write diagnostics must never replace the execution failure.
    """
    try:
        releases = {}
        for distribution in ("a13n-harness-ui", "a13n-harness", "pydantic-ai", "httpx2"):
            try:
                releases[distribution] = version(distribution)
            except PackageNotFoundError:
                releases[distribution] = "unavailable"
        report = {
            "version": releases["a13n-harness-ui"],
            "releases": releases,
            "python": platform.python_version(),
            "platform": platform.system(),
            "phase": phase,
            "session_id": thread_id,
            "run_id": run_id,
            "exceptions": [
                {
                    "error": f"{type(item).__name__}: {item}",
                    "notes": vars(item).get("__notes__", []),
                    "frames": [
                        {"file": frame.f_code.co_filename, "line": line, "function": frame.f_code.co_name}
                        for frame, line in traceback.walk_tb(item.__traceback__)
                    ],
                }
                for item in _exception_chain(error)
            ],
            "recovery": "Resume uses the last successfully selected continuation. This report is not a checkpoint.",
        }
        # Serialize before opening the file so diagnostic formatting errors leave
        # no partial file containing a misleading report.
        content = json.dumps(report, ensure_ascii=False, indent=2)
        descriptor, filename = tempfile.mkstemp(prefix="a13n-harness-ui-error-", suffix=".json")
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        location = f"Diagnostic report: {Path(filename)}."
    except Exception:
        location = "The diagnostic report could not be saved."
    return (
        f"{location} Review for sensitive content before attaching it to an issue. Nothing was uploaded.\n"
        f"Report a bug: {ISSUE_URL} (include reproduction steps and the reviewed report)."
    )


def _exception_chain(error: BaseException, seen: set[int] | None = None) -> Iterator[BaseException]:
    from a13n_harness import RunCleanupError

    seen = set() if seen is None else seen
    if id(error) in seen:
        return
    seen.add(id(error))
    yield error
    if error.__cause__ is not None:
        yield from _exception_chain(error.__cause__, seen)
    elif error.__context__ is not None and not error.__suppress_context__:
        yield from _exception_chain(error.__context__, seen)
    if isinstance(error, BaseExceptionGroup):
        for child in error.exceptions:
            yield from _exception_chain(child, seen)
    elif isinstance(error, RunCleanupError):
        for child in error.causes:
            yield from _exception_chain(child, seen)
