"""Stream final Shell output into ordinary Run temporary files."""

from __future__ import annotations

import codecs
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing

from a13n_harness.context import AgentContext
from a13n_harness.environment.commands import ProcessStreamRead

from .shell_results import ShellOutputFile

SHELL_OUTPUT_BYTES = 16 * 1024 * 1024
_BEARER = re.compile(r"(?i)\bBearer(?=\s)")
_TOKEN = re.compile(r"(?i)[A-Za-z0-9._~+/=-]+")
_SPACE = re.compile(r"\s+")


class _RedactedText:
    """Incremental UTF-8 and Bearer redaction with only a short prefix held back."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._pending = ""
        self._previous = " "
        self._state = "text"

    def feed(self, data: bytes, *, final: bool = False) -> bytes:
        text = self._pending + self._decoder.decode(data, final=final)
        self._pending = ""
        parts: list[str] = []
        while text:
            if self._state == "space":
                match = _SPACE.match(text)
                if match:
                    parts.append(match[0])
                    self._previous = match[0][-1]
                    text = text[match.end() :]
                    if not text:
                        break
                self._state = "text"
                if _TOKEN.match(text):
                    parts.append("[REDACTED]")
                    self._state = "token"
            if self._state == "token":
                match = _TOKEN.match(text)
                if match:
                    self._previous = match[0][-1]
                    text = text[match.end() :]
                    if not text:
                        break
                self._state = "text"
            match = _BEARER.search(self._previous + text, 1)
            if match:
                end = match.end() - 1
                parts.append(text[:end])
                text = text[end:]
                self._state = "space"
            else:
                end = len(text) if final else max(0, len(text) - 7)
                parts.append(text[:end])
                if end:
                    self._previous = text[end - 1]
                self._pending = text[end:]
                break
        return "".join(parts).encode("utf-8")


async def offload_stream(
    context: AgentContext | None,
    initial: ProcessStreamRead,
    read_page: Callable[[int], Awaitable[ProcessStreamRead]],
) -> ShellOutputFile:
    """Drain one finite source prefix before its native references are released."""
    saved: ShellOutputFile = {
        "path": None,
        "bytes": 0,
        "content_complete": False,
        "reason": None,
        "hint": "Temporary Run file; read or copy it before Run cleanup. File offsets are UTF-8 text bytes.",
    }

    async def chunks() -> AsyncGenerator[bytes]:
        page = initial
        offset = 0
        redactor = _RedactedText()
        while True:
            page_start = offset
            for chunk in page.chunks:
                if chunk.start_offset != offset:
                    saved["reason"] = "source_gap"
                    break
                data = chunk.data[: max(0, SHELL_OUTPUT_BYTES - offset)]
                offset += len(data)
                encoded = redactor.feed(data)
                remaining = SHELL_OUTPUT_BYTES - saved["bytes"]
                emitted = encoded[:remaining].decode("utf-8", errors="ignore").encode("utf-8")
                saved["bytes"] += len(emitted)
                yield emitted
                if len(data) != len(chunk.data) or len(encoded) > remaining:
                    saved["reason"] = "output_limit"
                    break
            if saved["reason"] is not None:
                break
            if offset >= page.capture.available_end:
                saved["content_complete"] = page.capture.content_complete and page.capture.observation_closed
                if not saved["content_complete"]:
                    saved["reason"] = page.capture.reason or (
                        "output_limit" if offset >= SHELL_OUTPUT_BYTES else "provider_incomplete"
                    )
                break
            if offset >= SHELL_OUTPUT_BYTES:
                saved["reason"] = "output_limit"
                break
            if offset == page_start:
                saved["reason"] = "source_gap"
                break
            try:
                page = await read_page(offset)
            except Exception:
                saved["reason"] = "read_failed"
                break
        tail = redactor.feed(b"", final=True)
        remaining = SHELL_OUTPUT_BYTES - saved["bytes"]
        emitted = tail[:remaining].decode("utf-8", errors="ignore").encode("utf-8")
        saved["bytes"] += len(emitted)
        yield emitted
        if len(tail) > remaining:
            saved["content_complete"] = False
            saved["reason"] = "output_limit"

    if context is not None:
        async with aclosing(chunks()) as stream:
            saved["path"] = await context._spill_shell_output(stream)
    if saved["path"] is None:
        saved.update(bytes=0, content_complete=False, reason="write_unavailable")
        saved["hint"] = "Temporary output could not be saved. The command was not replayed."
    return saved
