from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from a13n_ui.app import open_agent_ui_app
from a13n_ui.settings import AgentUiSettings, StorageSettings
from a13n_ui.tui.application import AgentUiTerminalApp
from a13n_ui.tui.models import ProjectionHints, TerminalLifecycle, TerminalState
from a13n_ui.tui.scheduler import ProjectionScheduler
from textual.widgets import Static


@pytest.mark.anyio
async def test_minimal_shell_paints_before_app_factory_finishes(tmp_path: Path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    @asynccontextmanager
    async def open_app() -> AsyncIterator[Any]:
        entered.set()
        await release.wait()
        settings = AgentUiSettings(storage=StorageSettings(data_root=tmp_path / "state"))
        async with open_agent_ui_app(settings) as live_app:
            yield live_app

    app = AgentUiTerminalApp(app_factory=open_app, launch_directory=tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        async with asyncio.timeout(1):
            await entered.wait()
        root = app.query_one("#terminal-root", Static)

        assert app.terminal_state.lifecycle is TerminalLifecycle.STARTING
        assert "Starting Agent UI" in str(root.render())

        release.set()
        async with asyncio.timeout(1):
            while app.terminal_state.lifecycle is not TerminalLifecycle.READY:
                await pilot.pause(0.01)

    await app.controller.close()


@pytest.mark.anyio
async def test_projection_scheduler_coalesces_to_latest_state_and_merges_hints() -> None:
    rendered: list[tuple[TerminalState, ProjectionHints]] = []

    async def render(state: TerminalState, hints: ProjectionHints) -> None:
        rendered.append((state, hints))

    scheduler = ProjectionScheduler(render, interval_seconds=0.01)
    first = TerminalState(logical_clock=1)
    second = TerminalState(logical_clock=2)
    await scheduler.submit(first, ProjectionHints(changed=frozenset({"focus"})))
    await scheduler.submit(
        second,
        ProjectionHints(changed=frozenset({"composer"}), scroll_to_latest=True),
    )
    await asyncio.sleep(0.03)

    assert len(rendered) == 1
    state, hints = rendered[0]
    assert state.logical_clock == 2
    assert hints.changed == frozenset({"focus", "composer"})
    assert hints.scroll_to_latest

    await scheduler.close()
