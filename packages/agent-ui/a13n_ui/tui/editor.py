"""Safe asynchronous external-editor handoff for terminal drafts."""

from __future__ import annotations

import asyncio
import os
import shlex
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from anyio import to_thread

from a13n_ui.errors import AgentUiError

MAX_EDITOR_BYTES = 1024 * 1024


def resolve_editor_command(environment: Mapping[str, str] | None = None) -> tuple[str, ...]:
    values = os.environ if environment is None else environment
    source = values.get("VISUAL") or values.get("EDITOR")
    if source is None or not source.strip():
        raise AgentUiError(
            "Set $VISUAL or $EDITOR before using the external editor.",
            code="editor_not_configured",
        )
    try:
        command = tuple(shlex.split(source, posix=os.name != "nt"))
    except ValueError as exc:
        raise AgentUiError("The external editor command is invalid.", code="editor_command_invalid") from exc
    if not command:
        raise AgentUiError("The external editor command is empty.", code="editor_command_invalid")
    return command


async def edit_text(command: Sequence[str], text: str) -> str:
    """Run an editor without a shell and return one bounded UTF-8 draft."""

    encoded = text.encode("utf-8")
    if len(encoded) > MAX_EDITOR_BYTES:
        raise AgentUiError("The draft is too large for external editing.", code="editor_draft_too_large")
    path = await to_thread.run_sync(_create_file, encoded)
    try:
        process = await asyncio.create_subprocess_exec(*command, os.fspath(path))
        return_code = await process.wait()
        if return_code != 0:
            raise AgentUiError(
                f"The external editor exited with status {return_code}.",
                code="editor_process_failed",
            )
        return await to_thread.run_sync(_read_file, path)
    except FileNotFoundError as exc:
        raise AgentUiError(
            f"The external editor executable was not found: {command[0]}",
            code="editor_executable_missing",
        ) from exc
    finally:
        await to_thread.run_sync(_remove_file, path)


def _create_file(content: bytes) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix="a13n-ui-draft-", suffix=".md", text=False)
    path = Path(raw_path)
    try:
        os.chmod(path, 0o600)
        file = os.fdopen(descriptor, "wb")
        descriptor = -1
        with file:
            file.write(content)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    return path


def _read_file(path: Path) -> str:
    size = path.stat().st_size
    if size > MAX_EDITOR_BYTES:
        raise AgentUiError("The edited draft exceeds the size limit.", code="editor_result_too_large")
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentUiError("The edited draft is not valid UTF-8.", code="editor_result_invalid_utf8") from exc


def _remove_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


__all__ = ["MAX_EDITOR_BYTES", "edit_text", "resolve_editor_command"]
