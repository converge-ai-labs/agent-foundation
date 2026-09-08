"""Styled projection of the bounded, plain-text /ps observation snapshot."""

from __future__ import annotations

from rich import box
from rich.panel import Panel
from rich.text import Text

from .theme import ResolvedTheme, activity_colors


def process_panel(source: str, theme: ResolvedTheme) -> Panel:
    """Keep state and command prominent while retaining copyable identities."""
    colors = activity_colors(theme)
    title, _, body = source.rstrip("\n").partition("\n")
    content = Text(overflow="fold")
    for index, line in enumerate(body.splitlines()):
        if index:
            content.append("\n")
        identifier, _, remainder = line.partition(" · ")
        phase, _, remainder = remainder.partition(" · ")
        command, run_separator, run_id = remainder.rpartition(" · Run ")
        if not run_separator:
            # Inventory and uncertainty notices remain explicit, not color-only.
            warning = "omitted" in line or "incomplete" in line
            content.append(line, style=colors["waiting" if warning else "muted"])
            continue
        state = (
            "running"
            if phase == "running"
            else "completed"
            if phase == "exited"
            else "failed"
            if phase == "timed_out" or phase.startswith("failed")
            else "waiting"
        )
        content.append(f"[{phase}] ", style=f"bold {colors[state]}")
        content.append(command, style="bold" if phase == "running" else "")
        content.append(f" · {identifier} · Run {run_id}", style=colors["muted"])
    return Panel(
        content,
        title=Text(title, style=f"bold {colors['running']}"),
        title_align="left",
        border_style=colors["running"],
        box=box.ROUNDED,
        padding=(0, 1),
    )
