"""Passive terminal themes, adapted from YAACLI; see YAACLI-LICENSE."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

type ThemePreference = Literal["auto", "dark", "light"]
type ThemeVariant = Literal["dark", "light"]
type ThemeSource = Literal["config", "colorfgbg", "fallback"]

_XTERM_COLOR_LEVELS = (0, 95, 135, 175, 215, 255)
_ANSI_COLORS = (
    (0, 0, 0),
    (128, 0, 0),
    (0, 128, 0),
    (128, 128, 0),
    (0, 0, 128),
    (128, 0, 128),
    (0, 128, 128),
    (192, 192, 192),
    (128, 128, 128),
    (255, 0, 0),
    (0, 255, 0),
    (255, 255, 0),
    (0, 0, 255),
    (255, 0, 255),
    (0, 255, 255),
    (255, 255, 255),
)


@dataclass(frozen=True, slots=True)
class RGBColor:
    """An RGB terminal color with 8-bit channels."""

    red: int
    green: int
    blue: int

    @property
    def is_light(self) -> bool:
        """Classify the color by its perceived brightness."""
        brightness = (299 * self.red + 587 * self.green + 114 * self.blue) / 1000
        return brightness >= 128


@dataclass(frozen=True, slots=True)
class ResolvedTheme:
    """A concrete UI and syntax-highlighting theme."""

    variant: ThemeVariant
    syntax_theme: str
    source: ThemeSource


def resolve_theme(
    preference: ThemePreference,
    *,
    environ: Mapping[str, str] | None = None,
) -> ResolvedTheme:
    """Resolve a theme using passive metadata without writing to the terminal."""
    if preference == "dark" or preference == "light":
        return _resolved_theme(preference, "config")

    environment = os.environ if environ is None else environ
    colorfgbg_background = _background_from_colorfgbg(environment.get("COLORFGBG"))
    if colorfgbg_background is not None:
        return _resolved_theme("light" if colorfgbg_background.is_light else "dark", "colorfgbg")

    return _resolved_theme("dark", "fallback")


def fallback_theme(preference: object) -> ResolvedTheme:
    """Resolve without terminal I/O, for construction and non-interactive use."""
    if preference == "light":
        return _resolved_theme("light", "config")
    if preference == "dark":
        return _resolved_theme("dark", "config")
    return _resolved_theme("dark", "fallback")


def prompt_toolkit_style_rules(theme: ResolvedTheme) -> dict[str, str]:
    """Return prompt_toolkit style rules for a resolved theme."""
    if theme.variant == "light":
        return {
            "status-bar": "bg:#dbeafe fg:#1e3a5f",
            "status-bar.warning": "fg:#9a3412 bold",
            "task-pane": "bg:#f8fafc fg:#334155",
            "task-pane.summary": "bg:#e2e8f0 fg:#1e293b bold",
            "task-pane.status-active": "fg:#0369a1 bold",
            "task-pane.status-pending": "fg:#854d0e",
            "task-pane.status-completed": "fg:#15803d",
            "task-pane.status-blocked": "fg:#b91c1c",
            "model-selector": "bg:ansiwhite fg:ansiblack",
            "frame.border": "fg:#94a3b8",
            "frame.label": "fg:#0f172a bold",
            "session-selector": "bg:#f8fafc fg:#334155",
            "session-selector.frame": "bg:#f8fafc fg:#334155",
            "session-selector.title": "fg:#0f172a bold",
            "session-selector.count": "fg:#64748b",
            "session-selector.key": "fg:#2563eb bold",
            "session-selector.hint": "fg:#64748b",
            "session-selector.separator": "fg:#cbd5e1",
            "session-selector.header": "fg:#64748b bold",
            "session-selector.row": "fg:#334155",
            "session-selector.current": "fg:#2563eb bold",
            "session-selector.selection": "bg:#dbeafe fg:#0f172a bold",
            "session-selector.scroll": "fg:#64748b italic",
            "session-selector.section": "fg:#2563eb bold",
            "session-selector.detail-id": "fg:#475569",
            "session-selector.detail-label": "fg:#64748b bold",
            "session-selector.detail-value": "fg:#1e293b",
            "session-selector.empty": "fg:#94a3b8 italic",
            "completion-menu.completion": "bg:#f8fafc fg:#334155",
            "completion-menu.completion.current": "bg:#bfdbfe fg:#1e3a5f bold",
            "completion-menu.meta.completion": "bg:#f8fafc fg:#64748b",
            "completion-menu.meta.completion.current": "bg:#bfdbfe fg:#1e3a5f",
            "input-area": "",
        }
    return {
        "status-bar": "bg:#1e3a5f fg:#e2e8f0",
        "status-bar.warning": "fg:#fbbf24 bold",
        "task-pane": "bg:#111827 fg:#cbd5e1",
        "task-pane.summary": "bg:#1f2937 fg:#f1f5f9 bold",
        "task-pane.status-active": "fg:#38bdf8 bold",
        "task-pane.status-pending": "fg:#facc15",
        "task-pane.status-completed": "fg:#4ade80",
        "task-pane.status-blocked": "fg:#f87171",
        "model-selector": "bg:ansibrightblack fg:ansiwhite",
        "frame.border": "fg:#475569",
        "frame.label": "fg:#f8fafc bold",
        "session-selector": "bg:#111827 fg:#cbd5e1",
        "session-selector.frame": "bg:#111827 fg:#cbd5e1",
        "session-selector.title": "fg:#f8fafc bold",
        "session-selector.count": "fg:#94a3b8",
        "session-selector.key": "fg:#60a5fa bold",
        "session-selector.hint": "fg:#94a3b8",
        "session-selector.separator": "fg:#334155",
        "session-selector.header": "fg:#94a3b8 bold",
        "session-selector.row": "fg:#cbd5e1",
        "session-selector.current": "fg:#60a5fa bold",
        "session-selector.selection": "bg:#1e3a5f fg:#f8fafc bold",
        "session-selector.scroll": "fg:#94a3b8 italic",
        "session-selector.section": "fg:#60a5fa bold",
        "session-selector.detail-id": "fg:#94a3b8",
        "session-selector.detail-label": "fg:#94a3b8 bold",
        "session-selector.detail-value": "fg:#e2e8f0",
        "session-selector.empty": "fg:#64748b italic",
        "completion-menu.completion": "bg:#111827 fg:#cbd5e1",
        "completion-menu.completion.current": "bg:#1d4ed8 fg:#eff6ff bold",
        "completion-menu.meta.completion": "bg:#111827 fg:#94a3b8",
        "completion-menu.meta.completion.current": "bg:#1d4ed8 fg:#dbeafe",
        "input-area": "",
    }


def _resolved_theme(variant: ThemeVariant, source: ThemeSource) -> ResolvedTheme:
    syntax_theme = "ansi_light" if variant == "light" else "ansi_dark"
    return ResolvedTheme(variant=variant, syntax_theme=syntax_theme, source=source)


def _background_from_colorfgbg(value: str | None) -> RGBColor | None:
    if not value:
        return None
    try:
        background_index = int(value.rsplit(";", maxsplit=1)[-1].strip())
    except ValueError:
        return None
    return _xterm_index_to_rgb(background_index)


def _xterm_index_to_rgb(index: int) -> RGBColor | None:
    if 0 <= index < len(_ANSI_COLORS):
        return RGBColor(*_ANSI_COLORS[index])
    if 16 <= index <= 231:
        cube_index = index - 16
        red = _XTERM_COLOR_LEVELS[cube_index // 36]
        green = _XTERM_COLOR_LEVELS[(cube_index % 36) // 6]
        blue = _XTERM_COLOR_LEVELS[cube_index % 6]
        return RGBColor(red=red, green=green, blue=blue)
    if 232 <= index <= 255:
        gray = 8 + (index - 232) * 10
        return RGBColor(red=gray, green=gray, blue=gray)
    return None
