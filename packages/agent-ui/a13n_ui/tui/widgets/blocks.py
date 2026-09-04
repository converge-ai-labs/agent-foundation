"""Bounded semantic timeline block widgets."""

from __future__ import annotations

from typing import ClassVar

from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container
from textual.widgets import Markdown, Static
from textual.widgets.markdown import MarkdownStream

from a13n_ui.tui.intents import OpenReview, SelectTimelineBlock
from a13n_ui.tui.models import BlockKind, BlockStatus, TimelineBlock
from a13n_ui.tui.widgets.messages import IntentRequested


class TimelineBlockWidget(Container):
    """Keep one mounted body per stable semantic block identity."""

    can_focus = True
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "open_detail", "Inspect", show=False),
    ]

    def __init__(
        self,
        block: TimelineBlock,
        *,
        continuation_id: str | None,
        show_reasoning: bool,
        show_tool_details: bool,
        widget_id: str,
    ) -> None:
        super().__init__(id=widget_id, classes=f"timeline-block block-{block.kind.value}")
        self.block = block
        self.continuation_id = continuation_id
        self.show_reasoning = show_reasoning
        self.show_tool_details = show_tool_details
        self._rendered_text = block.source_text or ""
        self._stream: MarkdownStream | None = None

    def compose(self) -> ComposeResult:
        yield Static(_title(self.block), classes="block-title")
        if self.block.kind is BlockKind.ASSISTANT:
            yield Markdown(self._rendered_text, classes="block-body", id=f"{self.id}-markdown")
        else:
            yield Static(
                _safe_body(self.block, self.show_reasoning, self.show_tool_details),
                classes="block-body",
            )

    async def project(
        self,
        block: TimelineBlock,
        *,
        continuation_id: str | None,
        show_reasoning: bool,
        show_tool_details: bool,
    ) -> None:
        self.block = block
        self.continuation_id = continuation_id
        self.show_reasoning = show_reasoning
        self.show_tool_details = show_tool_details
        try:
            self.query_one(".block-title", Static).update(_title(block))
            if block.kind is BlockKind.ASSISTANT:
                await self._project_markdown(block)
            else:
                await self._stop_stream()
                self.query_one(".block-body", Static).update(_safe_body(block, show_reasoning, show_tool_details))
        except Exception:
            await self._stop_stream()
            body = self.query_one(".block-body")
            if isinstance(body, Static):
                body.update(Text("This block could not be rendered safely.", style="bold red"))
            else:
                await body.remove()
                await self.mount(
                    Static(
                        Text("This block could not be rendered safely.", style="bold red"),
                        classes="block-body",
                    )
                )

    async def _project_markdown(self, block: TimelineBlock) -> None:
        markdown = self.query_one(Markdown)
        text = block.source_text or ""
        can_append = text.startswith(self._rendered_text) and block.status in {
            BlockStatus.PROVISIONAL,
            BlockStatus.RUNNING,
        }
        if can_append and len(text) > len(self._rendered_text):
            if self._stream is None:
                self._stream = Markdown.get_stream(markdown)
            await self._stream.write(text[len(self._rendered_text) :])
        elif text != self._rendered_text:
            await self._stop_stream()
            await markdown.update(text)
        self._rendered_text = text
        if block.status not in {BlockStatus.PROVISIONAL, BlockStatus.RUNNING}:
            await self._stop_stream()

    async def _stop_stream(self) -> None:
        if self._stream is not None:
            stream = self._stream
            self._stream = None
            await stream.stop()

    async def on_unmount(self) -> None:
        await self._stop_stream()

    def on_focus(self) -> None:
        self.post_message(IntentRequested(SelectTimelineBlock(self.block.thread_id, self.block.block_id)))

    def action_open_detail(self) -> None:
        intent = _review_intent(self.block, self.continuation_id)
        if intent is not None:
            self.post_message(IntentRequested(intent))


def _title(block: TimelineBlock) -> Text:
    label = {
        BlockKind.USER: "YOU",
        BlockKind.ASSISTANT: "ASSISTANT",
        BlockKind.REASONING: "REASONING",
        BlockKind.TOOL: "TOOL",
        BlockKind.CHILD: "CHILD",
        BlockKind.TASK: "TASK",
        BlockKind.NOTICE: "NOTICE",
        BlockKind.FAILURE: "FAILURE",
    }[block.kind]
    suffix = "" if block.status is BlockStatus.CLOSED else f"  {block.status.value}"
    return Text(f"{label}{suffix}", style="bold")


def _safe_body(
    block: TimelineBlock,
    show_reasoning: bool,
    show_tool_details: bool,
) -> Text | Syntax:
    try:
        return _body(block, show_reasoning, show_tool_details)
    except Exception:
        return Text("This block could not be rendered safely.", style="bold red")


def _body(block: TimelineBlock, show_reasoning: bool, show_tool_details: bool) -> Text | Syntax:
    source = block.source_text or ""
    if block.kind is BlockKind.REASONING and not show_reasoning:
        return Text(block.summary or "Reasoning content is collapsed.", style="dim")
    if block.kind is BlockKind.TOOL and not show_tool_details:
        return Text(block.summary or source or "Tool activity", style="cyan")
    if block.kind is BlockKind.TOOL and source:
        return Syntax(source, "json", word_wrap=True)
    summary = block.summary or ""
    if summary and source and summary != source:
        return Text.assemble((summary, "bold"), "\n", source)
    return Text(source or summary or "No displayable content.", style="red" if block.kind is BlockKind.FAILURE else "")


def _review_intent(block: TimelineBlock, continuation_id: str | None) -> OpenReview | None:
    if block.kind is BlockKind.TOOL:
        if continuation_id is None or block.retained_position is None or block.tool_call_id is None:
            return None
        return OpenReview(
            kind="retained",
            thread_id=block.thread_id,
            continuation_id=continuation_id,
            position=block.retained_position,
            tool_call_id=block.tool_call_id,
        )
    if block.kind is BlockKind.TASK and continuation_id is not None and block.task_id is not None:
        return OpenReview(
            kind="task",
            thread_id=block.thread_id,
            continuation_id=continuation_id,
            task_id=block.task_id,
        )
    if block.kind is BlockKind.CHILD and block.execution_id is not None:
        return OpenReview(
            kind="child",
            thread_id=block.thread_id,
            execution_id=block.execution_id,
        )
    return None


__all__ = ["TimelineBlockWidget"]
