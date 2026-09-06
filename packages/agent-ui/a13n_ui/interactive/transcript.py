"""Bounded semantic display cache, independent of durable App history."""

from __future__ import annotations

from bisect import bisect_right
from collections import OrderedDict
from dataclasses import dataclass, field
from io import StringIO

from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.controls import UIContent, UIControl
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from rich.console import Console
from rich.style import Style as RichStyle
from rich.text import Text

from .markdown import TerminalMarkdown
from .theme import ResolvedTheme, resolve_theme

_TRUNCATED = "[Display truncated; /history reads retained content]\n"


def bounded_text(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    budget = max(0, limit - len(_TRUNCATED.encode()))
    return _TRUNCATED + (encoded[-budget:].decode("utf-8", errors="ignore") if budget else "")


@dataclass(slots=True)
class Block:
    id: int
    source: str
    markdown: bool
    revision: int = 0
    cache_key: tuple[int, int, str] | None = None
    rows: list[StyleAndTextTuples] = field(default_factory=list)
    cache_bytes: int = 0


def _style(style: RichStyle | None) -> str:
    if style is None:
        return ""
    parts = []
    for key, color in (("fg", style.color), ("bg", style.bgcolor)):
        if color is not None and not color.is_default:
            parts.append(f"{key}:{color.get_truecolor().hex}")
    for key in ("bold", "italic", "underline", "strike"):
        enabled = {"bold": style.bold, "italic": style.italic, "underline": style.underline, "strike": style.strike}[
            key
        ]
        if enabled:
            parts.append(key)
    return " ".join(parts)


class Transcript:
    """Source and rendered caches have independent budgets and stable block IDs.

    A dirty block is parsed once per preview, never once per token. Rich segments
    become style-safe rows directly: raw ANSI is neither retained nor sliced.
    """

    def __init__(
        self,
        *,
        max_bytes: int = 2 * 1024 * 1024,
        max_blocks: int = 500,
        block_bytes: int = 128 * 1024,
        max_rows: int = 4000,
    ) -> None:
        self.max_bytes = max_bytes
        self.max_blocks = max_blocks
        self.block_bytes = min(block_bytes, max_bytes)
        self.max_rows = max_rows
        self.blocks: OrderedDict[int, Block] = OrderedDict()
        self.next_id = 1
        self.evicted = False
        self.theme: ResolvedTheme = resolve_theme("auto")
        self.rows: list[StyleAndTextTuples] = []
        self.ends: list[int] = []
        self.ids: list[int] = []
        self.dirty = True
        self.width = 0
        self.source_bytes = 0

    def append(self, source: str, *, markdown: bool = False) -> int:
        block_id = self.next_id
        self.next_id += 1
        source = bounded_text(source, self.block_bytes)
        self.blocks[block_id] = Block(block_id, source, markdown)
        self.source_bytes += len(source.encode("utf-8"))
        self._trim()
        self.dirty = True
        return block_id

    def extend(self, block_id: int, delta: str) -> bool:
        block = self.blocks.get(block_id)
        if block is None:
            return False
        self.source_bytes -= len(block.source.encode("utf-8"))
        block.source = bounded_text(block.source + delta, self.block_bytes)
        self.source_bytes += len(block.source.encode("utf-8"))
        block.revision += 1
        self._trim()
        self.dirty = True
        return True

    def _trim(self) -> None:
        while len(self.blocks) > 1 and (len(self.blocks) > self.max_blocks or self.source_bytes > self.max_bytes):
            _, block = self.blocks.popitem(last=False)
            self.source_bytes -= len(block.source.encode("utf-8"))
            self.evicted = True

    def render(self, width: int) -> None:
        width = max(1, min(4096, width))
        if not self.dirty and self.width == width:
            return
        self.width = width
        console = Console(file=StringIO(), width=width, force_terminal=True, color_system="truecolor")
        cache_bytes = 0
        total_rows = 0
        for block in reversed(self.blocks.values()):
            key = (block.revision, width, self.theme.variant)
            if block.cache_key != key:
                value = (
                    TerminalMarkdown(block.source, code_theme=self.theme.syntax_theme, hyperlinks=False)
                    if block.markdown
                    else Text(block.source)
                )
                # render_lines pads to viewport width by default; padding adds
                # memory and makes copied code contain invisible trailing spaces.
                lines = console.render_lines(value, console.options, pad=False)
                block.rows = [
                    [(_style(segment.style), segment.text) for segment in line if not segment.control]
                    for line in lines[-self.max_rows :]
                ]
                if len(lines) > self.max_rows:
                    block.rows[0] = [("class:warning", _TRUNCATED.strip())]
                block.rows.append([])
                block.cache_key = key
                block.cache_bytes = sum(
                    len(fragment[1].encode("utf-8")) + len(fragment[0]) + 64 for row in block.rows for fragment in row
                )
            cache_bytes += block.cache_bytes
            total_rows += len(block.rows)
            if cache_bytes > self.max_bytes or total_rows > self.max_rows:
                # Keep semantic sources for reflow, but evict old render caches.
                # The newest block itself is bounded by whole rows, never ANSI.
                room = max(1, self.max_rows - (total_rows - len(block.rows)))
                block.rows = block.rows[-room:]
                while len(block.rows) > 1 and sum(
                    len(fragment[1].encode("utf-8")) + len(fragment[0]) + 64 for row in block.rows for fragment in row
                ) > max(128, self.max_bytes - (cache_bytes - block.cache_bytes)):
                    block.rows = block.rows[len(block.rows) // 2 :]
                block.rows[0] = [("class:warning", _TRUNCATED.strip())]
                block.cache_bytes = sum(
                    len(fragment[1].encode("utf-8")) + len(fragment[0]) + 64 for row in block.rows for fragment in row
                )
                for older in self.blocks.values():
                    if older.id >= block.id:
                        break
                    older.rows = []
                    older.cache_key = None
                    older.cache_bytes = 0
                self.evicted = True
                break
        self.rows = []
        self.ids = []
        self.ends = []
        for block in self.blocks.values():
            if block.rows:
                self.rows.extend(block.rows)
                self.ends.append(len(self.rows))
                self.ids.append(block.id)
        self.dirty = False

    def anchor(self, row: int) -> tuple[int, int] | None:
        if not self.ids:
            return None
        row = max(0, min(row, len(self.rows) - 1))
        index = bisect_right(self.ends, row)
        start = self.ends[index - 1] if index else 0
        return self.ids[index], row - start

    def locate(self, anchor: tuple[int, int] | None) -> int:
        if anchor is None or anchor[0] not in self.ids:
            return 0
        index = self.ids.index(anchor[0])
        start = self.ends[index - 1] if index else 0
        return min(self.ends[index] - 1, start + anchor[1])


class TranscriptControl(UIControl):
    def __init__(self, transcript: Transcript) -> None:
        self.transcript = transcript
        self.follow = True
        self.position: tuple[int, int] | None = None
        self.height = 1
        self.cursor_row = 0
        self.top = 0

    def create_content(self, width: int, height: int) -> UIContent:
        self.height = max(1, height)
        self.transcript.render(width)
        count = max(1, len(self.transcript.rows))
        self.top = (
            max(0, count - self.height)
            if self.follow
            else min(max(0, count - self.height), self.transcript.locate(self.position))
        )
        self.cursor_row = count - 1 if self.follow else self.top
        return UIContent(
            get_line=lambda index: self.transcript.rows[index] if index < len(self.transcript.rows) else [],
            line_count=count,
            cursor_position=Point(x=0, y=self.cursor_row),
            show_cursor=False,
        )

    def scroll(self, amount: int) -> None:
        self.follow = False
        target = max(0, min(len(self.transcript.rows) - self.height, self.top + amount))
        self.position = self.transcript.anchor(target)
        self.cursor_row = target
        self.top = target

    def latest(self) -> None:
        self.follow = True
        self.position = None

    def mouse_handler(self, mouse_event: MouseEvent) -> None:
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.scroll(-3)
        elif mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self.scroll(3)
