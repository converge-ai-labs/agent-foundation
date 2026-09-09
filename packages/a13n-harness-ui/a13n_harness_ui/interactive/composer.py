"""Composer row measurements shared by growth and prompt-toolkit scrolling."""

from __future__ import annotations

from prompt_toolkit.formatted_text import fragment_list_to_text
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import GetLinePrefixCallable, UIContent
from prompt_toolkit.layout.screen import Char


def wrapped_height(text: str, width: int) -> int:
    """Count indivisible screen characters after the composer's three-cell prefix."""
    width = max(1, width - 3)
    rows, column = 1, 0
    for char in text:
        cells = Char(char).width
        if column + cells > width:
            rows += 1
            column = 0
        column += cells
    return rows


class _ComposerContent(UIContent):
    def get_height_for_line(
        self,
        lineno: int,
        width: int,
        get_line_prefix: GetLinePrefixCallable | None,
        slice_stop: int | None = None,
    ) -> int:
        text = fragment_list_to_text(self.get_line(lineno))
        # BufferControl includes the trailing cursor cell. Scrolling must also
        # measure the cell AT the cursor, not only the text preceding it.
        if slice_stop is not None:
            text = text[: slice_stop + 1]
        return wrapped_height(text, width)


class ComposerWindow(Window):
    def _scroll_when_linewrapping(self, ui_content: UIContent, width: int, height: int) -> None:
        # Keep native rendering, cursor mapping, and scroll policy; correct only
        # its aggregate-width measurement, which misses wide-character gaps.
        content = _ComposerContent(
            get_line=ui_content.get_line,
            line_count=ui_content.line_count,
            cursor_position=ui_content.cursor_position,
        )
        super()._scroll_when_linewrapping(content, width, height)
