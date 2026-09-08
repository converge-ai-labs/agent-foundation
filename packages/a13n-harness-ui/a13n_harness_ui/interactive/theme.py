"""Passive terminal themes."""

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
    if theme.source != "config":
        return {
            "": "bg:default fg:default",
            "status-bar": "fg:ansibrightblack",
            "status-bar.warning": "fg:ansiyellow bold",
            "task-pane": "",
            "frame.border": "fg:ansibrightblack",
            "frame.label": "fg:ansicyan bold",
            "session-selector.title": "bold",
            "session-selector.key": "fg:ansicyan",
            "session-selector.hint": "fg:ansibrightblack",
            "session-selector.selection": "reverse bold",
            "completion-menu.completion": "bg:default fg:default",
            "completion-menu.completion.current": "reverse",
            "completion-menu.meta.completion": "fg:ansibrightblack",
            "completion-menu.meta.completion.current": "reverse",
            "input-area": "",
            "input-area.prompt": "fg:ansigreen bold",
            "input-area.continuation": "fg:ansibrightblack",
            "input-area.border": "fg:ansibrightblack",
            "input-area.label": "fg:ansicyan bold",
            "input-area.hint": "fg:ansibrightblack",
            "selected": "reverse",
        }
    light = theme.variant == "light"
    background, foreground = ("#f8fafc", "#1e293b") if light else ("#121820", "#dbe4ee")
    surface, muted = ("#e9eef4", "#526176") if light else ("#1b2533", "#a0afc2")
    accent, border = ("#116f65", "#64748b") if light else ("#76d4c4", "#62758d")
    selected = "#cce8e3" if light else "#284840"
    warning = "#9a3412" if light else "#fbbf24"
    return {
        "": f"bg:{background} fg:{foreground}",
        "status-bar": f"bg:{surface} fg:{muted}",
        "status-bar.warning": f"fg:{warning} bold",
        "task-pane": f"bg:{surface} fg:{foreground}",
        "frame.border": f"fg:{border}",
        "frame.label": f"fg:{accent} bold",
        "session-selector.title": f"fg:{foreground} bold",
        "session-selector.key": f"fg:{accent}",
        "session-selector.hint": f"fg:{muted}",
        "session-selector.selection": f"bg:{selected} fg:{foreground} bold",
        "completion-menu.completion": f"bg:{surface} fg:{foreground}",
        "completion-menu.completion.current": f"bg:{selected} fg:{foreground} bold",
        "completion-menu.meta.completion": f"bg:{surface} fg:{muted}",
        "completion-menu.meta.completion.current": f"bg:{selected} fg:{foreground}",
        "input-area": f"bg:{surface} fg:{foreground}",
        "input-area.prompt": f"fg:{accent} bold",
        "input-area.continuation": f"fg:{muted}",
        "input-area.border": f"bg:{surface} fg:{border}",
        "input-area.label": f"bg:{surface} fg:{accent} bold",
        "input-area.hint": f"bg:{surface} fg:{muted}",
        "selected": f"bg:{selected} fg:{foreground}",
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
