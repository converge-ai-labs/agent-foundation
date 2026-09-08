"""Bounded semantic display cache, independent of durable App history."""

from __future__ import annotations

from bisect import bisect_right
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from io import StringIO
from itertools import chain, islice, pairwise
from tempfile import TemporaryDirectory
from typing import overload

from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.controls import UIContent, UIControl
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from rich.color import ColorType
from rich.console import Console, Group
from rich.segment import Segment
from rich.style import Style as RichStyle
from rich.text import Text

from .markdown import TerminalMarkdown
from .rows import RowStore
from .theme import ResolvedTheme, activity_colors, resolve_theme

_TRUNCATED = "[Display truncated; /history reads retained content]\n"


def bounded_text(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    budget = max(0, limit - len(_TRUNCATED.encode()))
    return _TRUNCATED + (encoded[-budget:].decode("utf-8", errors="ignore") if budget else "")


def _tool_row(source: str, theme: ResolvedTheme) -> Text:
    """Style the generated name/status fields, leaving the payload literal."""
    colors = activity_colors(theme)
    if source.startswith(
        (
            "Read ",
            "Find ",
            "Search ",
            "List ",
            "Run ",
            "Call ",
            "Delegate ",
            "Steer ",
            "Modified:",
            "Exploring",
            "Explored",
        )
    ):
        value = Text(no_wrap=False, overflow="fold")
        for index, line in enumerate(source.splitlines()):
            if index:
                value.append("\n")
            label, separator, detail = line.partition(" ")
            value.append(label + separator, style=colors["muted"])
            value.append(detail, style="default")
        return value
    name, separator, remainder = source.partition(" | ")
    value = Text(name, style=colors["muted"], no_wrap=False, overflow="fold")
    if not separator:
        return value
    state, separator, detail = remainder.partition(" | ")
    if state.startswith(("failed", "denied", "timed out", "cancelled", "interrupted")):
        tone = "muted"
    elif state in {"completed", "created", "updated", "deleted", "already absent", "finished", "exit 0"}:
        tone = "completed"
    elif state == "running":
        tone = "running"
    elif state in {"waiting", "retry"}:
        tone = "waiting"
    else:
        tone = "muted"
    value.append(" | ", style=colors["muted"])
    value.append(state, style=colors[tone])
    value.append(separator, style=colors["muted"])
    value.append(detail, style="default")
    return value


@dataclass(slots=True)
class Block:
    id: int
    _source: str
    markdown: bool
    kind: str = "text"
    revision: int = 0
    cache_key: tuple[int, int, ResolvedTheme, bool] | None = None
    rows: Sequence[StyleAndTextTuples] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    size: int = 0
    collapsed_lines: int | None = None
    collapsed_chars: int | None = None
    preview_rows: Sequence[StyleAndTextTuples] = field(default_factory=list)
    preview: str | None = None
    streaming: bool = False
    concise_hidden: bool = False
    concise_anchor: int | None = None
    chunks: list[tuple[str, RowStore]] = field(default_factory=list)

    @property
    def source(self) -> str:
        if self.pending:
            self._source += "".join(self.pending)
            self.pending.clear()
        return self._source

    def close(self) -> None:
        if isinstance(self.preview_rows, RowStore):
            self.preview_rows.close()
        if isinstance(self.rows, RowStore):
            self.rows.close()
        for _, rows in self.chunks:
            rows.close()
        self.chunks.clear()


class TranscriptRows(Sequence[StyleAndTextTuples]):
    def __init__(self, transcript: Transcript) -> None:
        self.transcript = transcript

    def __len__(self) -> int:
        return self.transcript.ends[-1] if self.transcript.ends else 0

    @overload
    def __getitem__(self, index: int) -> StyleAndTextTuples: ...

    @overload
    def __getitem__(self, index: slice) -> list[StyleAndTextTuples]: ...

    def __getitem__(self, index: int | slice) -> StyleAndTextTuples | list[StyleAndTextTuples]:
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        transcript = self.transcript
        number = bisect_right(transcript.ends, index)
        start = transcript.ends[number - 1] if number else 0
        block = transcript.blocks[transcript.ids[number]]
        offset = index - start
        if (block.preview is not None or block.collapsed_chars is not None) and not transcript.detailed:
            return block.preview_rows[offset]
        if block.chunks:
            for _, rows in block.chunks:
                if offset < len(rows):
                    return rows[offset]
                offset -= len(rows)
            return []
        return block.rows[offset] if offset < len(block.rows) else []


def _style(style: RichStyle | None) -> str:
    if style is None:
        return ""
    parts = []
    for key, color in (("fg", style.color), ("bg", style.bgcolor)):
        if color is not None and not color.is_default:
            ansi = (
                "ansiblack",
                "ansired",
                "ansigreen",
                "ansiyellow",
                "ansiblue",
                "ansimagenta",
                "ansicyan",
                "ansiwhite",
                "ansibrightblack",
                "ansibrightred",
                "ansibrightgreen",
                "ansibrightyellow",
                "ansibrightblue",
                "ansibrightmagenta",
                "ansibrightcyan",
                "ansibrightwhite",
            )
            if color.type == ColorType.STANDARD and color.number is not None and color.number < 16:
                parts.append(f"{key}:{ansi[color.number]}")
            else:
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
        block_bytes: int | None = None,
        max_rows: int = 4000,
    ) -> None:
        self._cache_directory = TemporaryDirectory(prefix="a13n-harness-ui-rows-")
        self.max_bytes = max_bytes
        self.max_blocks = max_blocks
        self.block_bytes = min(block_bytes or max_bytes, max_bytes)
        self.max_rows = max_rows
        self.blocks: OrderedDict[int, Block] = OrderedDict()
        self.next_id = 1
        self.evicted = False
        self.theme: ResolvedTheme = resolve_theme("auto")
        self.rows = TranscriptRows(self)
        self.detailed = False
        self.ends: list[int] = []
        self.ids: list[int] = []
        self.dirty = True
        self.width = 0
        self.source_bytes = 0

    def append(
        self,
        source: str,
        *,
        markdown: bool = False,
        collapsed_lines: int | None = None,
        collapsed_chars: int | None = None,
        streaming: bool = False,
        kind: str = "text",
    ) -> int:
        block_id = self.next_id
        self.next_id += 1
        self.evicted |= len(source.encode("utf-8")) > self.block_bytes
        source = bounded_text(source, self.block_bytes)
        self.blocks[block_id] = Block(
            block_id,
            source,
            markdown,
            kind=kind,
            size=len(source.encode("utf-8")),
            collapsed_lines=collapsed_lines,
            collapsed_chars=collapsed_chars,
            streaming=streaming,
        )
        self.source_bytes += len(source.encode("utf-8"))
        self._trim()
        self.dirty = True
        return block_id

    def preview(self, block_id: int, text: str, lines: int = 1, *, limit: int = 1024) -> None:
        block = self.blocks.get(block_id)
        if block is not None:
            block.preview = (
                text if len(text) <= limit else text[: max(0, limit - 64)] + "\n… preview shortened · Ctrl+O details"
            )
            block.collapsed_lines = lines
            block.revision += 1
            self.dirty = True

    def replace(self, block_id: int, source: str, *, kind: str | None = None) -> bool:
        block = self.blocks.get(block_id)
        if block is None:
            return False
        block.close()
        if kind is not None:
            block.kind = kind
        self.source_bytes -= block.size
        block._source = bounded_text(source, self.block_bytes)
        block.pending.clear()
        block.size = len(block._source.encode("utf-8"))
        self.source_bytes += block.size
        block.revision += 1
        block.cache_key = None
        self._trim()
        self.dirty = True
        return True

    def extend(self, block_id: int, delta: str) -> bool:
        block = self.blocks.get(block_id)
        if block is None:
            return False
        size = len(delta.encode("utf-8"))
        block.pending.append(delta)
        block.size += size
        self.source_bytes += size
        if block.size > self.block_bytes:
            source = bounded_text(block.source, self.block_bytes)
            self.source_bytes -= block.size - len(source.encode("utf-8"))
            block._source = source
            block.size = len(source.encode("utf-8"))
            self.evicted = True
        block.revision += 1
        self._trim()
        self.dirty = True
        return True

    def _trim(self) -> None:
        while len(self.blocks) > 1 and (len(self.blocks) > self.max_blocks or self.source_bytes > self.max_bytes):
            _, block = self.blocks.popitem(last=False)
            self.source_bytes -= block.size
            block.close()
            self.evicted = True

    def complete(self, block_id: int) -> None:
        block = self.blocks.get(block_id)
        if block is not None:
            block.streaming = False
            block.revision += 1
            self.dirty = True

    def close(self) -> None:
        for block in self.blocks.values():
            block.close()
        self._cache_directory.cleanup()

    def render(self, width: int) -> None:
        width = max(1, min(4096, width))
        if not self.dirty and self.width == width:
            return
        self.width = width
        console = Console(file=StringIO(), width=width, force_terminal=True, color_system="truecolor")

        def rows(
            source: str, markdown: bool, kind: str = "text", *, folded: bool = False
        ) -> Iterator[StyleAndTextTuples]:
            value = (
                TerminalMarkdown(source, code_theme=self.theme.syntax_theme, hyperlinks=False)
                if markdown
                else Text(source.rstrip("\n"))
            )
            if kind == "notice":
                colors = activity_colors(self.theme)
                label = Text("System", style=f"bold {colors['running']}")
                if markdown:
                    value = Group(label, value)
                else:
                    label.append(" · ", style=colors["muted"])
                    label.append(source.rstrip("\n"), style=f"not bold {colors['muted']}")
                    value = label
            elif kind == "notes" and folded:
                colors = activity_colors(self.theme)
                title, separator, detail = source.partition(" · Ctrl+O details")
                value = Text(title, style=f"bold {colors['running']}", no_wrap=True, overflow="ellipsis")
                value.append(separator + detail, style=f"not bold {colors['muted']}")
            elif kind == "question_receipt" and folded:
                colors = activity_colors(self.theme)
                value = Text()
                for line in source.rstrip("\n").splitlines(keepends=True):
                    tone = "completed" if line.startswith("Answered · ") else "muted"
                    value.append(
                        line, style=colors[tone] if line.startswith(("Answered · ", "Not answered · ")) else "default"
                    )
            elif kind in {"tool", "command"} and folded:
                value = _tool_row(source.rstrip("\n"), self.theme)
            elif kind == "approval":
                from .approvals import approval_panel

                value = approval_panel(source, self.theme)
            elif kind in {"command", "edit", "info", "summary", "compact", "notes", "warning"}:
                from rich import box
                from rich.panel import Panel

                title, _, body = source.rstrip("\n").partition("\n")
                content: Text | TerminalMarkdown = Text(body, no_wrap=folded, overflow="ellipsis" if folded else "fold")
                if kind == "edit":
                    content = Text(no_wrap=False, overflow="fold")
                    for line in body.splitlines(keepends=True):
                        style = "green" if line.startswith("+") else "red" if line.startswith("-") else ""
                        content.append(line, style=style)
                    # Rich panel titles are single-line; keep long paths in the
                    # wrapping body so concise mode does not silently lose them.
                    content = Text.assemble((title + "\n", "bold"), content)
                elif markdown:
                    content = TerminalMarkdown(body, code_theme=self.theme.syntax_theme, hyperlinks=False)
                value = Panel(
                    content,
                    title=None
                    if kind == "edit"
                    else Text(
                        title,
                        style=f"bold {activity_colors(self.theme)['running']}"
                        if kind == "notes"
                        else ""
                        if kind == "command"
                        else "bold",
                    ),
                    title_align="left",
                    border_style="yellow" if kind == "warning" else "bright_black",
                    box=box.ROUNDED,
                    padding=(0, 1),
                )
            elif kind == "processes":
                from .processes import process_panel

                value = process_panel(source, self.theme)
            elif kind == "subagents":
                from .subagents import subagent_panel

                value = subagent_panel(source, self.theme)
            # Stream Rich lines into a disposable disk cache, not a giant padded grid.
            for line in Segment.split_and_crop_lines(
                console.render(value, console.options), width, pad=False, include_new_lines=False
            ):
                rendered = [(_style(segment.style), segment.text) for segment in line if not segment.control]
                text = "".join(segment.text for segment in line)
                accent = ""
                if kind == "thinking":
                    accent = "fg:ansimagenta italic"
                elif kind == "user":
                    accent = "fg:ansigreen"
                elif kind == "tool" and not folded:
                    accent = "fg:ansibrightblack"
                elif kind == "shell":
                    accent = "fg:ansicyan"
                elif kind != "notice" and not markdown and text.startswith("["):
                    accent = "fg:ansicyan bold"
                elif kind == "edit" and text.startswith(("+", "-")):
                    accent = "fg:ansigreen" if text.startswith("+") else "fg:ansired"
                yield [(style + " " + accent, text) for style, text in rendered]
            if kind not in {"tool", "shell", "edit", "command", "info", "summary", "compact", "notes", "processes"}:
                yield []

        self.ids = []
        self.ends = []
        count = 0
        for block, following in pairwise(chain(self.blocks.values(), (None,))):
            assert block is not None
            if not self.detailed and (block.concise_hidden or block.concise_anchor in self.blocks):
                continue
            key = (block.revision, width, self.theme, block.streaming)
            if block.cache_key != key:
                source = block.source
                if block.streaming and len(source) > 16384:
                    # Large in-progress messages use copy-safe plain previews. Stable
                    # chunks are never reparsed; completion reflows Markdown once.
                    old = block.chunks if block.cache_key and block.cache_key[1:3] == key[1:3] else []
                    chunks = []
                    for i, start in enumerate(range(0, len(source), 8192)):
                        text = source[start : start + 8192]
                        if i < len(old) and old[i][0] == text:
                            chunks.append(old[i])
                        else:
                            chunks.append(
                                (
                                    text,
                                    RowStore(
                                        rows(text, False, block.kind),
                                        directory=self._cache_directory.name,
                                        page_size=min(64, max(1, self.max_rows // 2)),
                                    ),
                                )
                            )
                    for item in old:
                        if item not in chunks:
                            item[1].close()
                    if not old:
                        block.close()
                    elif isinstance(block.rows, RowStore):
                        block.rows.close()
                    block.rows = []
                    block.chunks = chunks
                else:
                    block.close()
                    block.rows = RowStore(
                        rows(source, block.markdown, block.kind),
                        directory=self._cache_directory.name,
                        page_size=min(64, max(1, self.max_rows // 2)),
                    )
                if isinstance(block.preview_rows, RowStore):
                    block.preview_rows.close()
                if block.preview is not None:
                    preview_rows = rows(block.preview, False, block.kind, folded=True)
                    if block.kind in {"tool", "command", "edit", "question_receipt"}:
                        # Preview text is already semantically bounded. Physical
                        # wrapping must not discard continuations or later rows.
                        block.preview_rows = RowStore(
                            preview_rows,
                            directory=self._cache_directory.name,
                            page_size=min(64, max(1, self.max_rows // 2)),
                        )
                    else:
                        block.preview_rows = list(
                            islice(preview_rows, 64 if block.kind == "info" else block.collapsed_lines or 1)
                        )
                elif block.collapsed_chars is not None:
                    limit = block.collapsed_lines or 5
                    block.preview_rows = list(
                        islice(rows(source[: block.collapsed_chars], block.markdown, block.kind), limit)
                    )
                block.cache_key = key
            length = sum(len(item[1]) for item in block.chunks) if block.chunks else len(block.rows)
            if (block.preview is not None or block.collapsed_chars is not None) and not self.detailed:
                length = len(block.preview_rows)
            elif block.collapsed_lines is not None and not self.detailed:
                length = min(length, block.collapsed_lines)
            elif (
                block.kind == "thinking"
                and following is not None
                and following.kind in {"thinking", "tool", "command", "edit"}
            ):
                # The cached final row is our synthetic spacer, not model text.
                # Omit it before adjacent thinking and tool blocks. Keep sources,
                # Markdown paragraphs, and cached rows intact for other layouts.
                length -= 1
            count += max(1, length)
            self.ids.append(block.id)
            self.ends.append(count)
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
        for block in self.transcript.blocks.values():
            if isinstance(block.rows, RowStore):
                block.rows.pages.clear()
            if isinstance(block.preview_rows, RowStore):
                block.preview_rows.pages.clear()
            for _, rows in block.chunks:
                rows.pages.clear()
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
        target = max(0, min(len(self.transcript.rows) - self.height, self.top + amount))
        self.follow = amount > 0 and target >= max(0, len(self.transcript.rows) - self.height)
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
