"""Render one detached recent-history page, never replay execution events."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from .panels import tool_arguments, tool_preview, tool_result
from .rendering import Status, StreamRenderer, terminal_text
from .transcript import TranscriptControl, bounded_text

if TYPE_CHECKING:
    from a13n_harness_ui.surfaces import TranscriptPage, TranscriptPart


_HISTORY_BYTES = 256 * 1024
_HISTORY_PARTS = 100
_PART_BYTES = 32 * 1024


def restore_transcript(renderer: StreamRenderer, page: TranscriptPage) -> None:
    """Append a recent visible tail with independent byte/part and per-part bounds."""
    selected: list[tuple[TranscriptPart, str]] = []
    size = 0
    omitted = page.next_cursor is not None
    parts = (part for entry in reversed(page.entries) for part in reversed(entry.parts))
    for part in parts:
        if not part.metadata.display or part.kind in {"system", "other"}:
            continue
        if len(selected) >= _HISTORY_PARTS or _HISTORY_BYTES - size < 128:
            omitted = True
            break
        text = (
            part.text
            if part.text is not None
            else (
                "[Content omitted from history projection]"
                if part.value_omitted
                else part.value
                if isinstance(part.value, str)
                else json.dumps(part.value, ensure_ascii=False)
            )
        )
        safe = terminal_text(text)
        bounded = bounded_text(safe, min(_PART_BYTES, _HISTORY_BYTES - size))
        omitted |= bounded != safe or part.value_omitted
        size += len(bounded.encode("utf-8"))
        selected.append((part, bounded))
    renderer.transcript.evicted |= omitted
    if omitted:
        renderer.append(
            "Recent history only · older or oversized content omitted · Ctrl+T to browse retained messages\n",
            kind="notice",
        )
    for part, text in reversed(selected):
        if part.kind == "tool_call":
            name = part.tool_name or "tool"
            block = renderer.transcript.append(f"{name}\n{tool_arguments(name, text)}", kind="tool")
            renderer.transcript.preview(block, f"{name} · {tool_preview(text)}", 2)
        elif part.kind == "tool_result":
            name = part.tool_name or "tool"
            renderer.append(
                f"{name} · {tool_result(name, text)}",
                kind="tool",
                collapsed_lines=renderer.status.max_tool_result_lines,
            )
        else:
            user = part.kind in {"user", "media"}
            renderer.append(
                ("> " if user else "") + text,
                markdown=part.kind in {"assistant", "thinking"},
                kind="user" if user else "thinking" if part.kind == "thinking" else "text",
            )


class HistoryControl(TranscriptControl):
    def is_focusable(self) -> bool:
        return True


class HistoryBrowser:
    """One bounded display page; navigation never appends old messages to live output."""

    def __init__(
        self,
        status: Status,
        load: Callable[[str | None, str | None], Awaitable[TranscriptPage]],
        invalidate: Callable[[], None],
    ) -> None:
        self.status = status
        self.load = load
        self.invalidate = invalidate
        self.renderer = StreamRenderer(status)
        self.view = HistoryControl(self.renderer.transcript)
        self.page: TranscriptPage | None = None
        self.cursors: list[str | None] = [None]
        self.index = 0
        self.message = "Loading retained messages…"
        self.closed = False
        self.loading = False

    def title(self) -> str:
        return " Retained messages · " + self.message

    async def navigate(self, direction: int = 0) -> None:
        if self.loading or self.closed:
            return
        cursor = None
        index = 0
        if direction < 0:
            if self.page is None or self.page.next_cursor is None:
                return
            if self.index >= 127:
                self.message = "Navigation limit reached · End returns to latest"
                self.invalidate()
                return
            cursor, index = self.page.next_cursor, self.index + 1
            if cursor in self.cursors[:index]:
                self.message = "History cursor did not advance · End reloads"
                self.invalidate()
                return
        elif direction > 0:
            if self.index == 0:
                return
            index = self.index - 1
            cursor = self.cursors[index]
        self.loading = True
        self.message = "Loading…"
        self.invalidate()
        try:
            page = await self.load(cursor, self.page.continuation_id if direction and self.page else None)
            if self.closed:
                return
            renderer = StreamRenderer(self.status)
            renderer.transcript.theme = self.renderer.transcript.theme
            renderer.transcript.detailed = self.renderer.transcript.detailed
            try:
                restore_transcript(renderer, page)
            except BaseException:
                renderer.transcript.close()
                raise
            self.renderer.transcript.close()
            self.renderer = renderer
            self.view.transcript = renderer.transcript
            self.view.latest()
            if direction > 0:
                self.view.follow = False
                self.view.position = None
                self.view.top = 0
            if not direction:
                self.cursors = [None]
            elif index == len(self.cursors):
                self.cursors.append(cursor)
            self.index, self.page = index, page
            self.message = "PgUp at top loads older" if page.next_cursor else "Beginning of retained history"
            if index:
                self.message += " · PgDn at bottom loads newer"
            if not page.entries:
                self.message = "No retained messages"
        except Exception as exc:
            self.message = f"{exc} · End reloads latest"
        finally:
            self.loading = False
            self.invalidate()

    async def scroll(self, amount: int) -> None:
        at_top = self.view.top == 0
        at_bottom = self.view.top >= max(0, len(self.view.transcript.rows) - self.view.height)
        if amount < 0 and at_top:
            await self.navigate(-1)
        elif amount > 0 and at_bottom and self.index:
            await self.navigate(1)
        else:
            self.view.scroll(amount)
            self.invalidate()

    def close(self) -> None:
        self.closed = True
        self.renderer.transcript.close()
