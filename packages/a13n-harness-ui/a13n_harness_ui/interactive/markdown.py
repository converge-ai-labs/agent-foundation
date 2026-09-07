"""Copy-safe Markdown, adapted from YAACLI; see YAACLI-LICENSE."""

from typing import ClassVar

from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import CodeBlock, Markdown
from rich.syntax import Syntax


class TerminalCodeBlock(CodeBlock):
    """Render fenced code with a copy-safe, terminal-aware background."""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        code = str(self.text).rstrip()
        syntax = Syntax(
            code,
            self.lexer_name,
            theme=self.theme,
            word_wrap=True,
            background_color="default",
            padding=0,
        )
        # Rendering highlighted Text directly avoids Syntax's shared line width.
        # Resetting justification prevents Rich from padding non-transparent
        # background styles to the full console width.
        highlighted = syntax.highlight(code)
        highlighted.justify = "default"
        yield highlighted


class TerminalMarkdown(Markdown):
    """Markdown renderer with distinguishable fenced code blocks."""

    elements: ClassVar = {
        **Markdown.elements,
        "fence": TerminalCodeBlock,
        "code_block": TerminalCodeBlock,
    }
