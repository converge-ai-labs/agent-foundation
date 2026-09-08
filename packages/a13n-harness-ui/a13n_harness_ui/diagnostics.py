"""Private local exception reports and opt-in open-source issue feedback."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import platform
import tempfile
import traceback
from collections.abc import Iterator, Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ISSUE_URL = "https://github.com/converge-ai-labs/agent-foundation/issues/new"


def exception_feedback(
    error: BaseException,
    *,
    thread_id: str | None,
    run_id: str | None = None,
    phase: str,
    loop_context: Mapping[str, object] | None = None,
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
        if loop_context is not None:
            report["event_loop"] = _event_loop_details(loop_context)
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


def terminal_traceback(error: BaseException) -> str:
    """Expand startup failures on stderr without locals or raw provider payloads."""
    from a13n_harness.errors import DefinitionError

    from a13n_harness_ui.errors import HarnessUiError

    lines = ["Traceback (exception chain; most recent call last):"]
    for item in _exception_chain(error):
        for frame, line in traceback.walk_tb(item.__traceback__):
            lines.append(f'  File "{frame.f_code.co_filename}", line {line}, in {frame.f_code.co_name}')
        if isinstance(item, HarnessUiError | DefinitionError):
            lines.append(f"{type(item).__name__} [{item.code}]: {item}")
        elif isinstance(item, BaseExceptionGroup):
            lines.append(f"{type(item).__name__}: {len(item.exceptions)} nested exception(s)")
        else:
            lines.append(f"{type(item).__name__}: details in the diagnostic report")
    return "\n".join(lines)


def _event_loop_details(context: Mapping[str, object]) -> dict[str, object]:
    """Allowlist locations, never repr tasks, callbacks, handles, or their arguments."""
    details: dict[str, object] = {}
    message = context.get("message")
    if isinstance(message, str):
        details["message"] = message[:4096]
    task = context.get("task", context.get("future"))
    if isinstance(task, asyncio.Task):
        details["task_name"] = task.get_name()[:256]
        details["task_done"] = task.done()
        details["task_cancelled"] = task.cancelled()
        coroutine = task.get_coro()
        if inspect.iscoroutine(coroutine):
            code = coroutine.cr_code
            details["coroutine"] = {
                "file": code.co_filename,
                "line": code.co_firstlineno,
                "function": code.co_qualname,
            }
        details["frames"] = [
            {"file": frame.f_code.co_filename, "line": frame.f_lineno, "function": frame.f_code.co_name}
            for frame in task.get_stack(limit=32)
        ]
    source = context.get("source_traceback")
    if isinstance(source, (list, tuple)):
        details["creation_frames"] = [
            {"file": frame.filename, "line": frame.lineno, "function": frame.name}
            for frame in source[-32:]
            if isinstance(frame, traceback.FrameSummary)
        ]
    return details


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
