"""Rich approval presentation, separate from continuation/authorization state."""

from __future__ import annotations

import json

from rich import box
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from .theme import ResolvedTheme, activity_colors


def approval_panel(source: str, theme: ResolvedTheme) -> Panel:
    """Render explicit fields, never infer sections from untrusted reason/command text."""
    from .rendering import terminal_text

    try:
        data = json.loads(source)
    except ValueError:
        # Display-cache eviction may leave a partial serialized preview.
        return Panel(Text(terminal_text(source)), title=Text("Approval preview incomplete"))
    colors = activity_colors(theme)
    accent = colors["waiting"]
    muted = colors["muted"]
    heading = colors["running"]

    def value(key: str) -> str:
        text = data.get(key, "")
        return terminal_text(text) if isinstance(text, str) else ""

    content: list[RenderableType] = []
    tool = Text(value("tool"), style="bold")
    tool.append("  ·  " + value("position"), style=f"not bold {muted}")
    content.append(tool)
    if value("risk") or value("reason") or value("error"):
        content.extend((Text(""), Text("Shell review", style=f"bold {heading}")))
        if value("error"):
            content.append(Text(value("error"), style=accent))
        else:
            risk = value("risk")
            risk_color = colors["failed"] if risk in {"high", "extra_high"} else accent
            row = Text("Risk    ", style=muted)
            row.append(risk.replace("_", " ").upper(), style=f"bold {risk_color}")
            content.append(row)
            reason = Text("Reason  ", style=muted)
            reason.append(value("reason"))
            content.append(reason)
    for key, label, lexer in (("command", "Command", "bash"), ("arguments", "Arguments", "json")):
        if value(key):
            content.extend((Text(""), Text(label, style=f"bold {heading}")))
            content.append(
                Syntax(value(key), lexer, theme=theme.syntax_theme, background_color="default", word_wrap=True)
            )
    if value("cwd"):
        cwd = Text("cwd  ", style=muted)
        cwd.append(value("cwd"), style=muted)
        content.append(cwd)
    if value("environment"):
        content.append(Text("env  " + value("environment"), style=muted))
    if value("context"):
        content.extend((Text(""), Text("Approval context", style=f"bold {heading}")))
        content.append(Text(value("context")))
    if value("notice"):
        content.extend((Text(""), Text(value("notice"), style=accent)))
    content.extend((Text(""), Text("No automatic approval · " + value("details"), style=muted)))
    return Panel(
        Group(*content),
        title=Text("Tool Approval Required", style=f"bold {accent}"),
        title_align="left",
        border_style=accent,
        box=box.ROUNDED,
        padding=(1, 2),
    )
