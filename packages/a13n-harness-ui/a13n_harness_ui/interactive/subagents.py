"""Styled projection of the bounded, plain-text /subagents inspection."""

from __future__ import annotations

from rich import box
from rich.panel import Panel
from rich.text import Text

from .theme import ResolvedTheme, activity_colors

_METADATA_PREFIXES = tuple(
    f'  "{key}":'
    for key in (
        "execution_id",
        "root_thread_id",
        "parent_thread_id",
        "child_thread_id",
        "child_run_id",
        "composition_id",
        "child_definition_id",
        "resumed_from",
        "created_at",
        "updated_at",
        "completed_at",
    )
)


def subagent_status(persisted_status: str, *, local_active: bool) -> str:
    """Saved running state alone is not evidence of local execution authority."""
    if persisted_status == "running":
        return "active" if local_active else "running (local execution unavailable)"
    return persisted_status


def _state_style(state: str, colors: dict[str, str]) -> str:
    role = {
        "active": "running",
        "succeeded": "completed",
        "failed": "failed",
        "lost": "failed",
        "cancelled": "waiting",
        "running (local execution unavailable)": "waiting",
        "unavailable": "waiting",
        "pending": "waiting",
    }.get(state, "muted")
    return f"bold {colors[role]}"


def subagent_panel(source: str, theme: ResolvedTheme) -> Panel:
    """Emphasize state/name without hiding identities, retained output or actions."""
    colors = activity_colors(theme)
    heading, _, body = source.rstrip("\n").partition("\n")
    listing = heading.startswith("Subagents · ")
    title = Text("Subagents", style=f"bold {colors['running']}")
    content = Text(overflow="fold")
    if listing:
        content.append(heading.removeprefix("Subagents · ") + "\n", style=colors["muted"])
    elif " · " in heading:
        name, _, state = heading.rpartition(" · ")
        # Unlike panel titles, body headers fold without losing status caveats.
        content.append(f"[{state}] ", style=_state_style(state, colors))
        content.append(name + "\n", style="bold")
    else:
        body = source.rstrip("\n")

    for index, line in enumerate(body.splitlines()):
        if index:
            content.append("\n")
        if listing and line.startswith("execution-"):
            identifier, _, remainder = line.partition(" · ")
            name, separator, state = remainder.rpartition(" · ")
            if separator:
                content.append(f"[{state}] ", style=_state_style(state, colors))
                content.append(name, style="bold")
                content.append(f" · {identifier}", style=colors["muted"])
                continue
        if line == "Subagent preview incomplete." or line.startswith("Conversation changed;"):
            style = colors["waiting"]
        elif listing or line.startswith(_METADATA_PREFIXES):
            style = colors["muted"]
        else:
            style = ""
        content.append(line, style=style)
    return Panel(
        content,
        title=title,
        title_align="left",
        border_style=colors["running"],
        box=box.ROUNDED,
        padding=(0, 1),
    )
