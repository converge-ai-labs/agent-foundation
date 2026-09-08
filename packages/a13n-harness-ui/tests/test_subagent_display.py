"""Subagent inspection hierarchy preserves saved-state uncertainty and evidence."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.backend import SessionBackend
from a13n_harness_ui.interactive.rendering import Status
from a13n_harness_ui.interactive.subagents import subagent_panel, subagent_status
from a13n_harness_ui.interactive.theme import activity_colors, resolve_theme
from a13n_harness_ui.interactive.transcript import Transcript
from a13n_harness_ui.surfaces import ReviewView
from rich.console import Console
from rich.style import Style
from rich.text import Text


@pytest.mark.parametrize("preference", ["auto", "dark", "light"])
def test_list_state_and_name_stand_out_without_highlighting_saved_running(preference: str) -> None:
    theme = resolve_theme(preference, environ={})
    colors = activity_colors(theme)
    source = (
        "Subagents · 3 shown · 21 executions\n"
        "execution-one · reviewer [bold] · active\n"
        "execution-two · researcher · running (local execution unavailable)\n"
        "execution-three · tester · succeeded\n"
        "More · /subagents next\n/subagents <execution-id> · details"
    )
    panel = subagent_panel(source, theme)
    text = panel.renderable
    assert isinstance(text, Text)
    console = Console()

    def style_at(value: str) -> Style:
        return text.get_style_at_offset(console, text.plain.index(value))

    assert style_at("[active]").color == Style(color=colors["running"]).color
    assert style_at("[running").color == Style(color=colors["waiting"]).color
    assert style_at("[succeeded]").color == Style(color=colors["completed"]).color
    assert style_at("reviewer [bold]").bold
    assert style_at("execution-one").color == Style(color=colors["muted"]).color
    assert not style_at("execution-one").bold
    assert "3 shown · 21 executions" in text.plain
    assert "More · /subagents next" in text.plain
    assert "/subagents <execution-id> · details" in text.plain


@pytest.mark.parametrize("width", [32, 80, 120])
@pytest.mark.parametrize("preference", ["auto", "dark", "light"])
def test_detail_reflows_uncertainty_ids_timestamps_actions_and_warning(width: int, preference: str) -> None:
    payload = {
        "execution_id": "execution-one",
        "created_at": "2026-09-08T00:00:00Z",
        "available_actions": [],
        "persisted_status": "running",
        "local_status": "unavailable",
    }
    source = (
        "reviewer · running (local execution unavailable)\n"
        "Retained [bold] output\n" + json.dumps(payload, indent=2) + "\nSubagent preview incomplete."
    )
    transcript = Transcript()
    try:
        block = transcript.append(source, kind="subagents")
        theme = resolve_theme(preference, environ={})
        transcript.theme = theme
        transcript.render(width)
        # Remove panel edges and wrapping whitespace, not source characters.
        rendered = "".join("".join(value for _, value in row).strip(" │") for row in transcript.rows)
        compact = "".join(rendered.split())
        for value in (
            "[running (local execution unavailable)] reviewer",
            "Retained [bold] output",
            "execution-one",
            "2026-09-08T00:00:00Z",
            '"available_actions": []',
            "Subagent preview incomplete.",
        ):
            assert "".join(value.split()) in compact
        assert transcript.blocks[block].source == source
        panel = subagent_panel(source, theme)
        text = panel.renderable
        assert isinstance(text, Text)
        colors = activity_colors(theme)
        for value in ("execution-one", "2026-09-08T00:00:00Z"):
            style = text.get_style_at_offset(Console(), text.plain.index(value))
            assert style.color == Style(color=colors["muted"]).color
        warning = text.get_style_at_offset(Console(), text.plain.index("Subagent preview incomplete."))
        assert warning.color == Style(color=colors["waiting"]).color
    finally:
        transcript.close()


@pytest.mark.parametrize(
    ("saved", "active", "expected"),
    [
        ("running", True, "active"),
        ("running", False, "running (local execution unavailable)"),
        ("succeeded", False, "succeeded"),
        ("failed", False, "failed"),
        ("lost", False, "lost"),
        ("cancelled", False, "cancelled"),
        ("succeeded", True, "succeeded"),
    ],
)
@pytest.mark.anyio
async def test_detail_uses_execution_state_not_review_lifecycle(saved: str, active: bool, expected: str) -> None:
    value = {"persisted_status": saved, "local_status": "active" if active else "unavailable"}
    review = ReviewView(
        kind="child",
        lifecycle="running" if saved == "running" else "closed",
        title="reviewer",
        summary="Saved output",
        value=value,
        truncated=True,
    )
    app = SimpleNamespace(child_review=AsyncMock(return_value=review))
    backend = SessionBackend(app, CliRequest(), Path.cwd(), Status())
    backend.thread_id = "thread-one"
    source = await backend.subagents("execution-one")
    assert source.startswith(f"reviewer · {expected}\n")
    assert "Saved output" in source
    assert json.dumps(value, indent=2) in source
    assert source.endswith("Subagent preview incomplete.")
    assert subagent_status(saved, local_active=active) == expected


@pytest.mark.parametrize(
    "source",
    [
        "No subagent executions.",
        "No next page. /subagents to refresh.",
        "Conversation changed; subagent inspection discarded. /subagents retries.",
        "execution-missing · unavailable\nThe selected child execution is unavailable.",
    ],
)
def test_empty_and_unavailable_inspections_remain_explicit(source: str) -> None:
    panel = subagent_panel(source, resolve_theme("auto", environ={}))
    assert isinstance(panel.renderable, Text)
    assert source.splitlines()[-1] in panel.renderable.plain
