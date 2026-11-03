"""User-owned prompt editing; external text never acquires attachment identity."""

from __future__ import annotations

import asyncio
import errno
import os
import shlex
import shutil
import signal
import tempfile
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import psutil

from .inline_attachments import InlineAttachments


class EditorDraftError(ValueError):
    """A rejected completed edit remains available in a private recovery file."""

    def __init__(self, message: str, edited: str) -> None:
        self.recovery_path = save_editor_draft(edited)
        super().__init__(f"{message} Your draft is preserved. Editor text saved to {self.recovery_path}")


def save_editor_draft(text: str) -> Path:
    """Keep a rejected edit independently of the composer's existing /recover slot."""
    directory = Path(tempfile.mkdtemp(prefix="a13n-prompt-recovery-"))
    path = directory / "prompt.md"
    path.touch(mode=0o600)
    path.write_text(text, encoding="utf-8")
    return path


def editor_command(editor: str) -> list[str]:
    if os.name == "nt":
        # Windows treats apostrophes literally and applies native backslash/quote
        # rules. A dummy executable makes every editor token an ordinary argument.
        import ctypes
        from ctypes import wintypes

        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        split = shell32.CommandLineToArgvW
        split.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
        split.restype = ctypes.POINTER(wintypes.LPWSTR)
        free = kernel32.LocalFree
        free.argtypes = (wintypes.HLOCAL,)
        free.restype = wintypes.HLOCAL
        count = ctypes.c_int()
        arguments = split("a13n-editor " + editor, ctypes.byref(count))
        if not arguments:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            command = list(arguments[index] for index in range(1, count.value))
        finally:
            free(ctypes.cast(arguments, wintypes.HLOCAL))
    else:
        command = shlex.split(editor)
    if not command or not command[0]:
        raise ValueError("VISUAL or EDITOR must name an executable.")
    command[0] = shutil.which(command[0]) or command[0]
    return command


def _foreground(group: int | None = None) -> int | None:
    """Transfer a controlling POSIX terminal without stopping the background parent."""
    if os.name != "posix":
        return None
    try:
        previous = os.tcgetpgrp(0)
    except OSError as error:
        if error.errno == errno.ENOTTY:
            return None  # Pipe fixtures and PTYs without a controlling session.
        raise
    if not previous:
        return None
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTTOU})
    try:
        os.tcsetpgrp(0, group if group is not None else previous)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
    return previous


async def _stop_editor(process: asyncio.subprocess.Process) -> None:
    if os.name == "posix":
        # The editor gets its own foreground group. Kill it even if the launcher
        # has exited, since a descendant can still own the terminal descriptors.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            pass
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    else:
        descendants: list[psutil.Process] = []
        with suppress(psutil.NoSuchProcess):
            descendants = psutil.Process(process.pid).children(recursive=True)
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
        # Retain identities before taskkill: a concurrently exiting launcher
        # must not prevent cleanup of its already-observed descendants.
        for child in descendants:
            with suppress(psutil.NoSuchProcess):
                child.kill()
        if descendants:
            await asyncio.to_thread(psutil.wait_procs, descendants, timeout=3)
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
    await process.wait()


async def _run_editor(command: list[str], path: Path) -> None:
    async def own_process() -> None:
        spawn = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *command,
                str(path),
                process_group=0 if os.name == "posix" else None,
            )
        )
        process: asyncio.subprocess.Process | None = None
        foreground: int | None = None
        try:
            try:
                process = await asyncio.shield(spawn)
            except asyncio.CancelledError:
                process = await spawn
                raise
            assert process is not None
            if os.name == "posix":
                foreground = _foreground(process.pid)
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGCONT)
            status = await process.wait()
            if status != 0:
                raise ValueError(f"Editor exited with status {status}. Your original draft is preserved.")
        except BaseException:
            if process is not None:
                await _stop_editor(process)
            raise
        finally:
            if foreground is not None:
                _foreground(foreground)

    # Forward cancellation once. Later cancellation requests cannot interrupt
    # process acquisition, tree termination, reaping, or terminal restoration.
    owner = asyncio.create_task(own_process())
    try:
        await asyncio.shield(owner)
    except asyncio.CancelledError:
        owner.cancel()
        while not owner.done():
            try:
                await asyncio.shield(owner)
            except asyncio.CancelledError:
                pass
        with suppress(asyncio.CancelledError):
            owner.result()
        raise


async def edit_prompt(text: str, attachments: InlineAttachments) -> str:
    """Edit UTF-8 text with inherited terminal I/O, preserving each attachment once."""
    editor = os.environ.get("VISUAL", "").strip() or os.environ.get("EDITOR", "").strip()
    if not editor:
        raise ValueError("Set VISUAL or EDITOR to edit your prompt, for example EDITOR='nano'.")
    command = editor_command(editor)

    # Each occurrence gets its own readable identity, even for a duplicated image.
    # A fresh namespace avoids interpreting the user's literal labels as attachments.
    prefix = f"[a13n-attachment-{uuid4().hex}-"
    markers: dict[str, str] = {}
    parts: list[str] = []
    for char in text:
        if char in attachments.values:
            marker = f"{prefix}{len(markers) + 1}: {attachments.values[char].label}]"
            markers[marker] = char
            parts.append(marker)
        else:
            parts.append(char)
    external = "".join(parts)
    with tempfile.TemporaryDirectory(prefix="a13n-prompt-") as directory:
        path = Path(directory) / "prompt.md"
        path.touch(mode=0o600)
        path.write_text(external, encoding="utf-8", newline="\n")
        await _run_editor(command, path)
        edited = path.read_text(encoding="utf-8-sig")
        if any(edited.count(marker) != 1 for marker in markers):
            raise EditorDraftError("Keep each attachment marker unchanged and exactly once.", edited)
        edited = attachments.external_text(edited)
        for marker, token in markers.items():
            edited = edited.replace(marker, token)
        return edited
