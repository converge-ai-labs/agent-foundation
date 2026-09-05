"""Bounded focused semantic timeline."""

from __future__ import annotations

import hashlib

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Button, Static

from a13n_ui.tui.intents import LoadOlderTranscript, SetFollowLatest, SetReadingAnchor
from a13n_ui.tui.models import ProjectionHints, ReadingAnchor, ThreadViewState, TimelineBlock
from a13n_ui.tui.widgets.blocks import TimelineBlockWidget
from a13n_ui.tui.widgets.messages import IntentRequested


class TimelineScroll(VerticalScroll):
    """Translate viewport-tail changes into explicit follow-latest intent."""

    def __init__(self) -> None:
        super().__init__(id="timeline-scroll")
        self.thread_id: str | None = None
        self.follow_latest = True
        self.projecting = False

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if self.projecting or self.thread_id is None or round(old_value) == round(new_value):
            return
        at_end = new_value >= max(0, self.max_scroll_y - 1)
        if at_end != self.follow_latest:
            self.follow_latest = at_end
            self.post_message(IntentRequested(SetFollowLatest(self.thread_id, at_end)))
        viewport_top = int(new_value)
        anchor = next(
            (widget for widget in self.query(TimelineBlockWidget) if widget.virtual_region.bottom > viewport_top),
            None,
        )
        if anchor is not None:
            self.post_message(
                IntentRequested(
                    SetReadingAnchor(
                        self.thread_id,
                        anchor.block.block_id,
                        max(0, viewport_top - anchor.virtual_region.y),
                    )
                )
            )


class TimelineView(Container):
    """Retain stable block widgets across streaming projections."""

    def __init__(self) -> None:
        super().__init__(id="timeline")
        self._focused_thread_id: str | None = None
        self._block_ids: tuple[str, ...] = ()
        self._widgets: dict[str, TimelineBlockWidget] = {}

    def compose(self) -> ComposeResult:
        yield Button("Load older history", id="timeline-older", variant="default")
        yield TimelineScroll()
        yield Button("Return to latest", id="timeline-latest", variant="primary")

    async def project(
        self,
        view: ThreadViewState,
        hints: ProjectionHints,
        *,
        show_reasoning: bool,
        show_tool_details: bool,
    ) -> None:
        self._focused_thread_id = view.thread_id
        older = self.query_one("#timeline-older", Button)
        latest = self.query_one("#timeline-latest", Button)
        scroll = self.query_one(TimelineScroll)
        older.display = view.older_cursor is not None
        latest.display = not view.follow_latest
        latest.label = f"Return to latest ({view.pending_output} new)" if view.pending_output else "Return to latest"
        scroll.thread_id = view.thread_id
        scroll.follow_latest = view.follow_latest

        await self._reconcile_blocks(
            view.timeline,
            continuation_id=view.transcript_continuation_id,
            show_reasoning=show_reasoning,
            show_tool_details=show_tool_details,
        )
        if hints.preserve_anchor is not None:
            self.restore_reading_anchor(hints.preserve_anchor)
        elif hints.scroll_to_latest and view.follow_latest:
            scroll.projecting = True
            scroll.scroll_end(animate=False, immediate=True)
            scroll.projecting = False

    def restore_reading_anchor(self, reading_anchor: ReadingAnchor) -> None:
        anchor = self._widgets.get(reading_anchor.block_id)
        if anchor is None:
            return
        scroll = self.query_one(TimelineScroll)
        scroll.projecting = True
        scroll.scroll_to(
            y=anchor.virtual_region.y + reading_anchor.line_offset,
            animate=False,
            immediate=True,
        )
        scroll.projecting = False

    async def _reconcile_blocks(
        self,
        blocks: tuple[TimelineBlock, ...],
        *,
        continuation_id: str | None,
        show_reasoning: bool,
        show_tool_details: bool,
    ) -> None:
        scroll = self.query_one(TimelineScroll)
        new_ids = tuple(block.block_id for block in blocks)
        if not blocks:
            if self._block_ids:
                await scroll.remove_children()
                await scroll.mount(Static("No retained or live activity yet.", id="timeline-empty", markup=False))
            self._block_ids = ()
            self._widgets.clear()
            return
        empty = scroll.query("#timeline-empty")
        if len(empty):
            await empty.remove()

        append_only = new_ids[: len(self._block_ids)] == self._block_ids
        prepend_count = len(new_ids) - len(self._block_ids)
        prepend_only = prepend_count >= 0 and new_ids[prepend_count:] == self._block_ids
        if not self._block_ids:
            append_only = True
        if append_only:
            for index, block in enumerate(blocks[len(self._block_ids) :], start=len(self._block_ids)):
                widget = self._new_block(block, continuation_id, show_reasoning, show_tool_details, index)
                self._widgets[block.block_id] = widget
                await scroll.mount(widget)
        elif prepend_only:
            additions: list[TimelineBlockWidget] = []
            for index, block in enumerate(blocks[:prepend_count]):
                widget = self._new_block(block, continuation_id, show_reasoning, show_tool_details, index)
                self._widgets[block.block_id] = widget
                additions.append(widget)
            if additions:
                await scroll.mount(*additions, before=0)
        else:
            await scroll.remove_children()
            self._widgets.clear()
            widgets = [
                self._new_block(block, continuation_id, show_reasoning, show_tool_details, index)
                for index, block in enumerate(blocks)
            ]
            self._widgets.update(zip(new_ids, widgets, strict=True))
            await scroll.mount(*widgets)

        self._block_ids = new_ids
        for block in blocks:
            widget = self._widgets[block.block_id]
            await widget.project(
                block,
                continuation_id=continuation_id,
                show_reasoning=show_reasoning,
                show_tool_details=show_tool_details,
            )

    @staticmethod
    def _new_block(
        block: TimelineBlock,
        continuation_id: str | None,
        show_reasoning: bool,
        show_tool_details: bool,
        index: int,
    ) -> TimelineBlockWidget:
        return TimelineBlockWidget(
            block,
            continuation_id=continuation_id,
            show_reasoning=show_reasoning,
            show_tool_details=show_tool_details,
            widget_id=f"timeline-block-{index}-{hashlib.sha256(block.block_id.encode()).hexdigest()[:12]}",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if self._focused_thread_id is None:
            return
        if event.button.id == "timeline-older":
            event.stop()
            self.post_message(IntentRequested(LoadOlderTranscript(self._focused_thread_id)))
        elif event.button.id == "timeline-latest":
            event.stop()
            self.post_message(IntentRequested(SetFollowLatest(self._focused_thread_id, True)))


__all__ = ["TimelineScroll", "TimelineView"]
