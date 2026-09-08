"""A detached, metadata-only session browser inside the terminal Application."""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import TextArea

from a13n_harness_ui.surfaces import ThreadMetadataMutation, ThreadMetadataPatch, ThreadPage, ThreadSummary

from .rendering import terminal_text

if TYPE_CHECKING:
    from prompt_toolkit.application import Application

    from .backend import SessionBackend


def _clip(text: str, width: int) -> str:
    text = " ".join(terminal_text(text).split())
    if get_cwidth(text) <= width:
        return text
    result = ""
    for char in text:
        if get_cwidth(result + char) > max(0, width - 1):
            break
        result += char
    return result + "…"


class ResumeBrowser:
    """Keep one result page; only explicit confirmation delegates a session switch."""

    def __init__(
        self,
        backend: SessionBackend,
        app: Application[None],
        resume: Callable[[str], Awaitable[None]],
        close: Callable[[], None],
        history: Callable[[str], None],
    ) -> None:
        self.backend, self.app = backend, app
        self.resume, self.close, self.history = resume, close, history
        self.page: ThreadPage | None = None
        self.cursors: list[str | None] = [None]
        self.page_index = 0
        self.index = 0
        self.all_directories = False
        self.locations: dict[str, str] = {}
        self.matching_projects: frozenset[str] = frozenset()
        self.message = "Loading…"
        self.loading = False
        self.saving = False
        self.closed = False
        self.renaming: ThreadSummary | None = None
        self._revision = 0
        self._load_task: asyncio.Task[None] | None = None
        self.search = TextArea(multiline=False, prompt=" Search: ", height=1, read_only=Condition(lambda: self.saving))
        self.name = TextArea(multiline=False, prompt=" Name: ", height=1, read_only=Condition(lambda: self.saving))
        self.search.buffer.on_text_changed += lambda _: self.reload(debounce=True)
        self.container = HSplit(
            [
                Window(FormattedTextControl(self.title), height=1, style="class:session-selector.title"),
                self.search,
                ConditionalContainer(self.name, filter=Condition(lambda: self.renaming is not None)),
                Window(FormattedTextControl(self.rows), height=self.row_height),
                ConditionalContainer(
                    Window(FormattedTextControl(self.preview), height=self.preview_height, wrap_lines=True),
                    filter=Condition(lambda: self.app.output.get_size().rows >= 10),
                ),
                Window(
                    FormattedTextControl(lambda: " " + terminal_text(self.message)),
                    height=self.message_height,
                    wrap_lines=True,
                ),
            ],
            key_bindings=self._bindings(),
        )

    @property
    def selected(self) -> ThreadSummary | None:
        if self.loading or self.page is None or not self.page.threads:
            return None
        return self.page.threads[self.index]

    def title(self) -> str:
        scope = "All directories" if self.all_directories else "Current directory"
        total = self.page.total if self.page else 0
        return f" Resume · {scope} · {total} sessions · page {self.page_index + 1}"

    def row_height(self) -> int:
        return max(
            1,
            self.app.output.get_size().rows
            - self.preview_height()
            - self.message_height()
            - 5
            - int(self.renaming is not None),
        )

    def message_height(self) -> int:
        size = self.app.output.get_size()
        return min(3, 1 + get_cwidth(self.message) // max(1, size.columns)) if size.rows >= 12 else 1

    def preview_height(self) -> int:
        rows = self.app.output.get_size().rows
        return min(10, rows // 3) if rows >= 10 else 0

    def rows(self) -> FormattedText:
        if self.page is None or not self.page.threads:
            return FormattedText([("", " Loading…" if self.loading else " No matching saved sessions.")])
        width = self.app.output.get_size().columns
        height = self.row_height()
        start = max(0, min(self.index - height // 2, len(self.page.threads) - height))
        result: list[tuple[str, str]] = []
        for index in range(start, min(len(self.page.threads), start + height)):
            item = self.page.threads[index]
            label = item.title or item.excerpt.first_input or "No messages yet"
            activity = item.activity_at or item.created_at
            suffix = f"  {activity:%Y-%m-%d %H:%M}" if width >= 70 else ""
            prefix = "> " if index == self.index else "  "
            style = "class:session-selector.selection" if index == self.index else ""
            result.append((style, prefix + _clip(label, width - len(suffix) - 2) + suffix + "\n"))
        return FormattedText(result)

    def guidance(self, item: ThreadSummary) -> str:
        project = item.configuration.project_id
        if project is None:
            return "Projectless session · inspect only; select a Project before resuming here."
        if project not in self.locations:
            return "Unresolved Project · inspect only; restore its configuration before resuming."
        directory = self.locations[project]
        if project not in self.matching_projects:
            return f"Open in {directory}: cd {shlex.quote(directory)} && a13n-harness-ui --resume {shlex.quote(item.thread_id)}"
        if item.root_activity.receipt_id is not None:
            return "Session is running · inspect only until it finishes."
        return directory

    def preview(self) -> str:
        item = self.selected
        if item is None:
            return ""
        width = max(1, self.app.output.get_size().columns - 1)
        excerpt = item.excerpt
        reply = {"none": "No saved reply", "progress": "Saved progress (not final)", "final": "Latest saved reply"}[
            excerpt.reply_kind
        ]
        # Reserve identity/location and give each excerpt a bounded share of the screen.
        lines = max(1, (self.preview_height() - 2) // 2)
        return "\n".join(
            [
                _clip(f" {item.thread_id} · {self.guidance(item)}", width),
                _clip(" Latest saved input: " + (excerpt.latest_input or "No saved input excerpt"), width * lines),
                _clip(f" {reply}: " + excerpt.latest_reply, width * lines),
                _clip(" Ctrl+T inspects retained messages without switching", width),
            ]
        )

    async def initialize(self) -> None:
        try:
            configuration = await self.backend.app.current_configuration()
            self.matching_projects = frozenset(await self.backend.app.cwd_project_ids(self.backend.directory))
            if configuration is not None:
                self.locations = {key: item.roots[0].path for key, item in configuration.projects.items()}
            if not self.closed:
                self.reload()
        except Exception as exc:
            if not self.closed:
                self.message = f"{exc} · F5 retries"
                self.app.invalidate()

    def reload(self, *, debounce: bool = False, page_index: int = 0, keep: str | None = None) -> None:
        if self.closed or self.saving:
            return
        self._revision += 1
        revision = self._revision
        if self._load_task is not None:
            self._load_task.cancel()
        self.loading = True
        self.message = "Loading…"
        query, scope = self.search.text, self.all_directories
        cursor = self.cursors[page_index] if page_index else None

        async def load() -> None:
            try:
                if debounce:
                    await asyncio.sleep(0.15)
                page = await self.backend.resume_sessions(query=query, all_directories=scope, cursor=cursor)
                if self.closed or revision != self._revision:
                    return
                self.page, self.page_index = page, page_index
                self.index = next((i for i, item in enumerate(page.threads) if item.thread_id == keep), 0)
                if not page_index:
                    self.cursors = [None]
                self.message = "Type to search names, IDs and saved excerpts · F5 refreshes"
            except Exception as exc:
                if not self.closed and revision == self._revision:
                    self.page = None
                    self.message = f"{exc} · F5 retries"
            finally:
                if not self.closed and revision == self._revision:
                    self.loading = False
                    self.app.invalidate()

        self._load_task = self.app.create_background_task(load())
        self.app.invalidate()

    def navigate(self, direction: int) -> None:
        if self.loading or self.saving or self.renaming is not None or self.page is None:
            return
        index = self.page_index + direction
        if index < 0:
            return
        if direction > 0:
            if self.page.next_cursor is None:
                return
            self.cursors = [*self.cursors[:index], self.page.next_cursor]
        self.reload(page_index=index)

    async def confirm(self) -> None:
        if self.saving:
            return
        item = self.renaming or self.selected
        if item is None:
            return
        self.saving = True
        self.message = "Saving name…" if self.renaming else "Opening session…"
        self.app.invalidate()
        try:
            if self.renaming is not None:
                await self.backend.app.update_thread_metadata(
                    thread_id=item.thread_id,
                    mutation=ThreadMetadataMutation(
                        expected_version=item.metadata_version,
                        patch=ThreadMetadataPatch(title=self.name.text.strip() or None),
                    ),
                )
                self.renaming = None
                self.app.layout.focus(self.search)
                self.saving = False
                self.reload(page_index=self.page_index, keep=item.thread_id)
            else:
                if item.configuration.project_id not in self.matching_projects:
                    raise ValueError(self.guidance(item))
                await self.resume(item.thread_id)
        except Exception as exc:
            self.message = f"{exc} · F5 refreshes"
        finally:
            self.saving = False
            self.app.invalidate()

    def shutdown(self) -> None:
        self.closed = True
        if self._load_task is not None:
            self._load_task.cancel()

    def _bindings(self) -> KeyBindings:
        keys = KeyBindings()

        @keys.add("escape", eager=True)
        @keys.add("c-c", eager=True)
        def back(event: KeyPressEvent) -> None:
            if self.saving:
                return
            if self.renaming is not None:
                self.renaming = None
                self.app.layout.focus(self.search)
            else:
                self.close()

        @keys.add("enter", eager=True)
        def confirm(event: KeyPressEvent) -> None:
            event.app.create_background_task(self.confirm())

        @keys.add("up", eager=True)
        @keys.add("down", eager=True)
        def select(event: KeyPressEvent) -> None:
            if self.selected is not None and not self.saving and self.renaming is None:
                assert self.page is not None
                self.index = max(
                    0, min(len(self.page.threads) - 1, self.index + (-1 if event.key_sequence[-1].key == "up" else 1))
                )

        @keys.add("pageup", eager=True)
        @keys.add("pagedown", eager=True)
        def page(event: KeyPressEvent) -> None:
            self.navigate(-1 if event.key_sequence[-1].key == "pageup" else 1)

        @keys.add("c-a", eager=True)
        def scope(event: KeyPressEvent) -> None:
            if not self.saving and self.renaming is None:
                self.all_directories = not self.all_directories
                self.reload()

        @keys.add("f5", eager=True)
        def refresh(event: KeyPressEvent) -> None:
            if not self.saving and self.renaming is None:
                event.app.create_background_task(self.initialize())

        @keys.add("f2", eager=True)
        def rename(event: KeyPressEvent) -> None:
            if self.selected is not None and not self.saving and self.renaming is None:
                self.renaming = self.selected
                self.name.text = self.selected.title or ""
                self.name.buffer.cursor_position = len(self.name.text)
                self.app.layout.focus(self.name)

        @keys.add("c-t", eager=True)
        def history(event: KeyPressEvent) -> None:
            if self.selected is not None and not self.saving and self.renaming is None:
                self.history(self.selected.thread_id)

        return keys
