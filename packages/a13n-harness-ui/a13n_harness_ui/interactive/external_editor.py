"""User-owned prompt editing; external text never acquires attachment identity."""

from __future__ import annotations

import asyncio
import os
import shlex
import tempfile
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from .inline_attachments import InlineAttachments


async def edit_prompt(text: str, attachments: InlineAttachments) -> str:
    """Edit UTF-8 text with inherited terminal I/O, preserving each attachment once."""
    editor = os.environ.get("VISUAL", "").strip() or os.environ.get("EDITOR", "").strip()
    if not editor:
        raise ValueError("Set VISUAL or EDITOR to edit your prompt, for example EDITOR='nano'.")
    lexer = shlex.shlex(editor, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    if os.name == "nt":
        lexer.escape = ""
    command = list(lexer)
    if not command:
        raise ValueError("VISUAL or EDITOR must name an executable.")

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
        # Shield process acquisition so cancellation cannot orphan a launched editor.
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(*command, str(path)))
        process: asyncio.subprocess.Process | None = None
        try:
            try:
                process = await asyncio.shield(spawn)
            except asyncio.CancelledError:
                process = await spawn
                raise
            assert process is not None
            status = await process.wait()
        finally:
            if process is not None and process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
        if status != 0:
            raise ValueError(f"Editor exited with status {status}. Your original draft is preserved.")
        edited = path.read_text(encoding="utf-8")
        if any(edited.count(marker) != 1 for marker in markers):
            raise ValueError(
                "Keep each attachment marker unchanged and exactly once. Your original draft is preserved."
            )
        edited = attachments.external_text(edited)
        for marker, token in markers.items():
            edited = edited.replace(marker, token)
        return edited
