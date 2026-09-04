from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from a13n_ui.errors import AgentUiError, LivePresentationError
from a13n_ui.surfaces import (
    AgentSourceView,
    ChildExecutionPage,
    LaunchProjectSelected,
    ProjectSummary,
    RootActivityState,
    RootActivityView,
    ThreadConfigurationView,
    ThreadDetail,
    ThreadFocusSnapshot,
    ThreadSummary,
    TranscriptPage,
    WorkbenchPage,
)
from a13n_ui.tui.controller import TerminalController
from a13n_ui.tui.intents import OpenFocus, RetryStartup
from a13n_ui.tui.models import ProjectionHints, TerminalLifecycle, TerminalState

NOW = datetime(2026, 9, 4, tzinfo=UTC)


class _EventStream:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[object] = asyncio.Queue()

    def __aiter__(self) -> AsyncIterator[Any]:
        return self

    async def __anext__(self) -> Any:
        item = await self.queue.get()
        if isinstance(item, BaseException):
            raise item
        return item


class _FakeApp:
    def __init__(self) -> None:
        self.summary_streams: list[_EventStream] = []
        self.focus_streams: list[_EventStream] = []
        self.summary_active = 0
        self.focus_active = 0
        self.max_focus_active = 0
        self.workbench_calls = 0

    async def resolve_launch_project(self, directory: Path, *, project_id: str | None = None) -> Any:
        del directory, project_id
        return LaunchProjectSelected(
            directory="/workspace",
            project=ProjectSummary(
                project_id="project-main",
                name="Main",
                position=0,
                roots=("/workspace",),
            ),
        )

    async def workbench(self, **kwargs: object) -> WorkbenchPage:
        del kwargs
        self.workbench_calls += 1
        return WorkbenchPage(project_id="project-main", rows=(), total=0)

    @asynccontextmanager
    async def summary_events(self, **kwargs: object) -> AsyncIterator[_EventStream]:
        del kwargs
        stream = _EventStream()
        self.summary_streams.append(stream)
        self.summary_active += 1
        try:
            yield stream
        finally:
            self.summary_active -= 1

    @asynccontextmanager
    async def watch_thread(self, *, root_thread_id: str, child_limit: int = 20) -> AsyncIterator[Any]:
        del child_limit
        stream = _EventStream()
        self.focus_streams.append(stream)
        self.focus_active += 1
        self.max_focus_active = max(self.max_focus_active, self.focus_active)
        try:
            yield SimpleNamespace(snapshot=_snapshot(root_thread_id), events=stream)
        finally:
            self.focus_active -= 1

    async def thread_decisions(self, **kwargs: object) -> None:
        del kwargs
        return None

    async def get_thread_transcript(self, **kwargs: object) -> TranscriptPage:
        thread_id = kwargs["thread_id"]
        assert isinstance(thread_id, str)
        return TranscriptPage(
            continuation_id="a" * 64,
            entries=(),
            total=0,
        )


def _snapshot(thread_id: str) -> ThreadFocusSnapshot:
    summary = ThreadSummary(
        thread_id=thread_id,
        created_at=NOW,
        updated_at=NOW,
        metadata_version=1,
        archived=False,
        configuration=ThreadConfigurationView(
            version=1,
            project_id="project-main",
            agent_source=AgentSourceView(kind="agent", id="agent-main"),
            environment_profile_id="environment-native",
        ),
        continuation_state="selected",
        root_activity=RootActivityView(state=RootActivityState.inactive),
    )
    return ThreadFocusSnapshot(
        epoch="live-1",
        cutover_sequence=0,
        thread=ThreadDetail(
            thread=summary,
            continuation_id="a" * 64,
            available_actions=("run", "archive"),
        ),
        children=ChildExecutionPage(executions=(), total=0),
    )


def _factory(app: _FakeApp, exits: list[str] | None = None) -> Callable[[], Any]:
    @asynccontextmanager
    async def open_app() -> AsyncIterator[Any]:
        try:
            yield app
        finally:
            if exits is not None:
                exits.append("closed")

    return open_app


def _renderer(states: list[TerminalState]) -> Callable[[TerminalState, ProjectionHints], Any]:
    async def render(state: TerminalState, hints: ProjectionHints) -> None:
        del hints
        states.append(state)

    return render


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_controller_installs_summary_before_startup_queries_and_owns_cleanup(tmp_path: Path) -> None:
    app = _FakeApp()
    exits: list[str] = []
    rendered: list[TerminalState] = []
    controller = TerminalController(
        app_factory=_factory(app, exits),
        render=_renderer(rendered),
        launch_directory=tmp_path,
    )

    await controller.start()

    assert controller.state.lifecycle is TerminalLifecycle.READY
    assert app.summary_active == 1
    assert app.workbench_calls == 1
    assert rendered[-1].lifecycle is TerminalLifecycle.READY

    await controller.close()

    assert app.summary_active == 0
    assert exits == ["closed"]
    assert controller.background_task_count == 0
    assert controller.state.lifecycle is TerminalLifecycle.CLOSING


@pytest.mark.anyio
async def test_controller_replaces_focus_without_overlapping_detailed_subscriptions(tmp_path: Path) -> None:
    app = _FakeApp()
    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
        launch_thread_id="thread-1",
    )
    await controller.start()
    await _wait_until(lambda: controller.state.thread_view("thread-1") is not None)

    await controller.handle(OpenFocus("thread-2"))
    await _wait_until(lambda: controller.state.thread_view("thread-2") is not None)

    assert controller.state.focused_thread_id == "thread-2"
    assert app.focus_active == 1
    assert app.max_focus_active == 1

    await controller.close()
    assert app.focus_active == 0


@pytest.mark.anyio
async def test_summary_gap_reinstalls_subscription_before_full_refresh(tmp_path: Path) -> None:
    app = _FakeApp()
    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
    )
    await controller.start()

    await app.summary_streams[0].queue.put(LivePresentationError("summary gap", code="summary_cursor_expired"))
    await _wait_until(lambda: len(app.summary_streams) == 2 and app.workbench_calls == 2)

    assert app.summary_active == 1
    await controller.close()


@pytest.mark.anyio
async def test_startup_failure_can_retry_with_a_fresh_app_context(tmp_path: Path) -> None:
    app = _FakeApp()
    attempts = 0

    @asynccontextmanager
    async def open_app() -> AsyncIterator[Any]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise AgentUiError("not ready", code="app_not_ready")
        yield app

    controller = TerminalController(
        app_factory=open_app,
        render=_renderer([]),
        launch_directory=tmp_path,
    )

    await controller.start()
    assert controller.state.lifecycle is TerminalLifecycle.FAILED

    await controller.handle(RetryStartup())
    assert controller.state.lifecycle is TerminalLifecycle.READY
    assert attempts == 2

    await controller.close()
