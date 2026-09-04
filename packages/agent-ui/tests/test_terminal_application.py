from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from a13n_ui.app import open_agent_ui_app
from a13n_ui.settings import AgentUiSettings, StorageSettings
from a13n_ui.surfaces import (
    ActivitySummary,
    AgentSourceView,
    AgentSummary,
    ChildExecutionPage,
    DecisionBatchView,
    EnvironmentProfileSummary,
    ProjectSummary,
    QuestionOptionView,
    QuestionView,
    ReviewView,
    RootActivityState,
    RootActivityView,
    SelectableResourceSummary,
    SkillCatalogItemView,
    SkillCatalogView,
    StructuredQuestionRequestView,
    ThreadConfigurationView,
    ThreadDetail,
    ThreadFocusSnapshot,
    ThreadSelectorCatalog,
    ThreadSummary,
    WorkbenchPage,
    WorkbenchThreadView,
)
from a13n_ui.tui.application import AgentUiTerminalApp, StateProjected
from a13n_ui.tui.intents import (
    ApplyCompletion,
    CloseOverlay,
    ExecuteCommand,
    InsertSkillReference,
    OpenFocus,
    OpenOverlay,
    SearchWorkbench,
    SelectConfigurationResource,
    SetWorkbenchFilter,
    StartNewDraft,
    SubmitComposer,
    SubmitDecisionSession,
    SubmitThreadDraft,
    UpdateDecisionDraft,
)
from a13n_ui.tui.models import (
    BlockKind,
    BlockStatus,
    CompletionState,
    ConfigurationConflictState,
    ControlMode,
    DecisionAnswerDraft,
    DecisionSessionState,
    DraftState,
    OverlayState,
    ProjectionHints,
    ReviewState,
    TerminalLifecycle,
    TerminalMode,
    TerminalState,
    ThreadViewState,
    TimelineBlock,
    WorkbenchState,
)
from a13n_ui.tui.scheduler import ProjectionScheduler
from textual.widgets import Input, ListView, Markdown, Static, TextArea


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
        status = app.query_one("#terminal-status", Static)

        assert app.terminal_state.lifecycle is TerminalLifecycle.STARTING
        assert "Starting Agent UI" in str(status.render())

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


NOW = datetime(2026, 9, 4, tzinfo=UTC)


def _focused_state(
    *,
    timeline: tuple[TimelineBlock, ...] = (),
    decisions: DecisionBatchView | None = None,
) -> TerminalState:
    summary = ThreadSummary(
        thread_id="thread-1",
        created_at=NOW,
        updated_at=NOW,
        metadata_version=1,
        title="Investigate failure",
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
    detail = ThreadDetail(
        thread=summary,
        continuation_id="a" * 64,
        available_actions=("run", "archive"),
    )
    snapshot = ThreadFocusSnapshot(
        epoch="live-1",
        cutover_sequence=0,
        thread=detail,
        children=ChildExecutionPage(executions=(), total=0),
    )
    session = None
    if decisions is not None:
        session = DecisionSessionState(
            thread_id="thread-1",
            continuation_id=decisions.continuation_id,
            request_ids=tuple(item.request_id for item in decisions.requests),
            answers=tuple(DecisionAnswerDraft(request_id=item.request_id) for item in decisions.requests),
        )
    return TerminalState(
        lifecycle=TerminalLifecycle.READY,
        mode=TerminalMode.FOCUS,
        launch_project_id="project-main",
        focused_thread_id="thread-1",
        drafts=(DraftState(key="thread-1"),),
        thread_views=(
            ThreadViewState(
                thread_id="thread-1",
                detail=detail,
                snapshot=snapshot,
                decisions=decisions,
                decision_session=session,
                timeline=timeline,
                control_mode=(ControlMode.AWAITING_DECISION if decisions is not None else ControlMode.IDLE),
                epoch="live-1",
                transcript_continuation_id="a" * 64,
            ),
        ),
    )


def _blocked_terminal_app(tmp_path: Path) -> tuple[AgentUiTerminalApp, asyncio.Event]:
    release = asyncio.Event()

    @asynccontextmanager
    async def open_app() -> AsyncIterator[Any]:
        await release.wait()
        raise AssertionError("blocked test App must not open")
        yield

    return AgentUiTerminalApp(app_factory=open_app, launch_directory=tmp_path), release


@pytest.mark.anyio
async def test_focus_composer_emits_typed_edit_and_submit_intents(tmp_path: Path) -> None:
    app, _release = _blocked_terminal_app(tmp_path)
    handled = AsyncMock()
    app.controller.handle = handled
    state = _focused_state()

    async with app.run_test(size=(100, 32)) as pilot:
        app.post_message(StateProjected(state, ProjectionHints(changed=frozenset({"focus"}))))
        await pilot.pause(0.05)
        editor = app.query_one("#composer-editor", TextArea)
        editor.load_text("check the failure")
        await pilot.pause(0.05)
        editor.focus()
        await pilot.press("enter")
        await pilot.pause(0.05)

        intents = [call.args[0] for call in handled.await_args_list]
        assert any(type(intent).__name__ == "EditDraft" for intent in intents)
        assert any(isinstance(intent, SubmitComposer) for intent in intents)

    await app.controller.close()


@pytest.mark.anyio
async def test_focus_keeps_one_markdown_widget_across_streaming_updates(tmp_path: Path) -> None:
    app, _release = _blocked_terminal_app(tmp_path)
    block = TimelineBlock(
        block_id="live:thread-1:run-1:root:part:message-1",
        thread_id="thread-1",
        run_id="run-1",
        kind=BlockKind.ASSISTANT,
        status=BlockStatus.RUNNING,
        source_text="Hello",
        provisional=True,
    )
    state = _focused_state(timeline=(block,))

    async with app.run_test(size=(100, 32)) as pilot:
        app.post_message(StateProjected(state, ProjectionHints(changed=frozenset({"focus"}))))
        await pilot.pause(0.1)
        markdown = app.query_one(Markdown)
        assert markdown.source == "Hello"

        updated_block = replace(block, source_text="Hello world", version=2)
        updated_view = replace(state.thread_views[0], timeline=(updated_block,))
        updated = replace(state, thread_views=(updated_view,))
        app.post_message(
            StateProjected(
                updated,
                ProjectionHints(changed=frozenset({"focus"}), scroll_to_latest=True),
            )
        )
        await pilot.pause(0.1)

        assert app.query_one(Markdown) is markdown
        assert markdown.source == "Hello world"

    await app.controller.close()


@pytest.mark.anyio
async def test_decision_and_review_surfaces_emit_explicit_actions_only(tmp_path: Path) -> None:
    app, _release = _blocked_terminal_app(tmp_path)
    handled = AsyncMock()
    app.controller.handle = handled
    decisions = DecisionBatchView(
        continuation_id="a" * 64,
        requests=(
            StructuredQuestionRequestView(
                request_id="request-1",
                tool_name="ask_user_question",
                questions=(
                    QuestionView(
                        header="Storage",
                        question="Where should counters be stored?",
                        options=(
                            QuestionOptionView(label="Redis", description="Shared sliding window"),
                            QuestionOptionView(label="Memory", description="Per process"),
                        ),
                    ),
                ),
            ),
        ),
    )
    state = _focused_state(decisions=decisions)

    async with app.run_test(size=(100, 34)) as pilot:
        app.post_message(StateProjected(state, ProjectionHints(changed=frozenset({"focus"}))))
        await pilot.pause(0.05)
        await pilot.click("#decision-option-0")
        await pilot.click("#decision-submit")
        await pilot.pause(0.05)

        intents = [call.args[0] for call in handled.await_args_list]
        assert any(isinstance(intent, UpdateDecisionDraft) for intent in intents)
        assert any(isinstance(intent, SubmitDecisionSession) for intent in intents)

        handled.reset_mock()
        await pilot.press("escape")
        await pilot.pause(0.02)
        assert handled.await_count == 0

        review = ReviewView(
            lifecycle="pending",
            kind="diff",
            title="Pending edit",
            content="--- a/file\n+++ b/file\n-old\n+new",
            truncated=True,
        )
        review_state = replace(
            state,
            overlays=(OverlayState(kind="review", key="review-1"),),
            review=ReviewState(key="review-1", view=review, request_version=1),
        )
        app.post_message(StateProjected(review_state, ProjectionHints(changed=frozenset({"overlay"}))))
        await pilot.pause(0.05)
        assert app.query_one("#review-pane").display
        await pilot.click("#review-close")
        await pilot.pause(0.02)
        assert any(isinstance(call.args[0], CloseOverlay) for call in handled.await_args_list)

    await app.controller.close()


def _workbench_state() -> TerminalState:
    focused = _focused_state()
    thread = focused.thread_views[0].detail
    assert thread is not None
    row = WorkbenchThreadView(
        thread=thread.thread,
        project_name="Main",
        agent_name="Agent",
        environment_name="Full Control",
        latest_activity=ActivitySummary(kind="assistant", text="Finished inspection", occurred_at=NOW),
        available_actions=("open", "archive"),
    )
    return replace(
        focused,
        mode=TerminalMode.WORKBENCH,
        project_filter_id="project-main",
        workbench=WorkbenchState(
            page=WorkbenchPage(project_id="project-main", rows=(row,), total=1),
            selected_thread_id="thread-1",
            projection_version=1,
        ),
    )


@pytest.mark.anyio
async def test_workbench_navigation_search_and_prompt_preserve_one_terminal_state(tmp_path: Path) -> None:
    app, _release = _blocked_terminal_app(tmp_path)
    handled = AsyncMock()
    app.controller.handle = handled
    state = _workbench_state()

    async with app.run_test(size=(120, 36)) as pilot:
        app.post_message(StateProjected(state, ProjectionHints(changed=frozenset({"workbench"}))))
        await pilot.pause(0.1)

        assert app.query_one("#workbench-screen").display
        assert "Finished inspection" in str(app.query_one("#workbench-preview-body", Static).render())

        search = app.query_one("#workbench-search", Input)
        search.value = "failure"
        await pilot.pause(0.3)
        editor = app.query_one("#workbench-editor", TextArea)
        editor.load_text("continue")
        await pilot.pause(0.05)
        editor.focus()
        await pilot.press("enter")
        await pilot.pause(0.05)
        list_view = app.query_one("#workbench-list", ListView)
        list_view.focus()
        await pilot.press("enter")
        await pilot.pause(0.05)

        intents = [call.args[0] for call in handled.await_args_list]
        assert any(isinstance(intent, SearchWorkbench) and intent.query == "failure" for intent in intents)
        assert any(isinstance(intent, SubmitThreadDraft) for intent in intents)
        assert any(isinstance(intent, OpenFocus) for intent in intents)

        await pilot.click("#workbench-new")
        await pilot.pause(0.05)
        new_draft = next(call.args[0] for call in handled.await_args_list if isinstance(call.args[0], StartNewDraft))
        assert new_draft.defaults is not None
        assert new_draft.defaults.project_id == "project-main"

        await pilot.resize_terminal(60, 28)
        await pilot.pause(0.05)
        assert app.has_class("width-narrow")
        await pilot.resize_terminal(130, 36)
        await pilot.pause(0.05)
        assert app.has_class("width-wide")
        assert app.terminal_state.workbench.selected_thread_id == "thread-1"

    await app.controller.close()


def _overlay_catalogs() -> tuple[
    ThreadSelectorCatalog,
    tuple[ProjectSummary, ...],
    SkillCatalogView,
]:
    selectors = ThreadSelectorCatalog(
        agents=(
            AgentSummary(
                agent_id="agent-main",
                name="Main Agent",
                model_id="model-main",
                source_path="agents/main.yaml",
            ),
            AgentSummary(
                agent_id="agent-alt",
                name="Alternate Agent",
                model_id="model-alt",
                source_path="agents/alt.yaml",
            ),
        ),
        environments=(
            EnvironmentProfileSummary(
                profile_id="environment-native",
                name="Full Control",
                mode="full-control",
                description="Native host access",
                provider_key="native",
                release_owned=True,
                canonical_host_paths=True,
            ),
        ),
        harness_plugins=(
            SelectableResourceSummary(
                resource_id="plugin-review",
                name="Review Plugin",
                kind="harness_plugin",
                source_path="plugins/review.yaml",
            ),
        ),
        environment_run_extensions=(),
        mcp_servers=(),
    )
    projects = (
        ProjectSummary(
            project_id="project-main",
            name="Main",
            position=0,
            roots=("/workspace",),
        ),
    )
    skills = SkillCatalogView(
        catalog_id="c" * 64,
        context_kind="idle",
        items=(
            SkillCatalogItemView(
                item_id="d" * 64,
                name="review",
                description="Review code",
                source_id="project-main",
                logical_path="skills/review/SKILL.md",
            ),
        ),
    )
    return selectors, projects, skills


@pytest.mark.anyio
async def test_command_and_resource_overlays_emit_exact_typed_intents(tmp_path: Path) -> None:
    app, _release = _blocked_terminal_app(tmp_path)
    handled = AsyncMock()
    app.controller.handle = handled
    state = _focused_state()
    selectors, projects, skills = _overlay_catalogs()

    async with app.run_test(size=(110, 34)) as pilot:
        app.post_message(StateProjected(state, ProjectionHints(changed=frozenset({"focus"}))))
        await pilot.pause(0.05)
        await pilot.press("ctrl+p")
        await pilot.pause(0.05)
        assert any(isinstance(call.args[0], OpenOverlay) for call in handled.await_args_list)

        handled.reset_mock()
        editor = app.query_one("#composer-editor", TextArea)
        editor.focus()
        command_state = replace(state, overlays=(OverlayState(kind="commands"),))
        app.post_message(StateProjected(command_state, ProjectionHints(changed=frozenset({"overlay"}))))
        await pilot.pause(0.1)
        assert app.focused is not None and app.focused.id == "overlay-search"
        assert app.query_one("#focus-screen").disabled
        overlay_list = app.query_one("#overlay-list", ListView)
        overlay_list.focus()
        overlay_list.index = 0
        await pilot.press("enter")
        await pilot.pause(0.05)
        assert any(isinstance(call.args[0], ExecuteCommand) for call in handled.await_args_list)

        handled.reset_mock()
        configuration_state = replace(
            state,
            selectors=selectors,
            overlays=(OverlayState(kind="configuration", key="agent", context_key="thread-1"),),
            configuration_conflict=ConfigurationConflictState(
                thread_id="thread-1",
                kind="agent",
                resource_id="agent-alt",
                intended_selected=True,
                message="Configuration changed; review the newer state and retry.",
            ),
        )
        app.post_message(StateProjected(configuration_state, ProjectionHints(changed=frozenset({"overlay"}))))
        await pilot.pause(0.1)
        assert "review the newer state" in str(app.query_one("#overlay-body", Static).render())
        overlay_list.focus()
        overlay_list.index = 1
        await pilot.press("enter")
        await pilot.pause(0.05)
        assert any(
            isinstance(call.args[0], SelectConfigurationResource) and call.args[0].resource_id == "agent-alt"
            for call in handled.await_args_list
        )

        handled.reset_mock()
        skill_state = replace(
            state,
            skill_catalog=skills,
            overlays=(OverlayState(kind="skills"),),
        )
        app.post_message(StateProjected(skill_state, ProjectionHints(changed=frozenset({"overlay"}))))
        await pilot.pause(0.1)
        overlay_list.focus()
        overlay_list.index = 0
        await pilot.press("enter")
        await pilot.pause(0.05)
        assert any(
            isinstance(call.args[0], InsertSkillReference) and call.args[0].reference.item_id == "d" * 64
            for call in handled.await_args_list
        )

        handled.reset_mock()
        project_state = replace(
            _workbench_state(),
            projects=projects,
            overlays=(OverlayState(kind="projects"),),
        )
        app.post_message(StateProjected(project_state, ProjectionHints(changed=frozenset({"overlay"}))))
        await pilot.pause(0.1)
        overlay_list.focus()
        overlay_list.index = 1
        await pilot.press("enter")
        await pilot.pause(0.05)
        assert any(
            isinstance(call.args[0], SetWorkbenchFilter) and call.args[0].project_id == "project-main"
            for call in handled.await_args_list
        )

        app.post_message(StateProjected(state, ProjectionHints(changed=frozenset({"overlay"}))))
        await pilot.pause(0.05)
        assert not app.query_one("#focus-screen").disabled
        assert app.focused is editor

    await app.controller.close()


@pytest.mark.anyio
async def test_completion_popup_emits_exact_skill_identity(tmp_path: Path) -> None:
    app, _release = _blocked_terminal_app(tmp_path)
    handled = AsyncMock()
    app.controller.handle = handled
    state = _focused_state()
    _selectors, _projects, skills = _overlay_catalogs()
    state = replace(
        state,
        drafts=(DraftState(key="thread-1", text="use $rev", cursor=8),),
        completion=CompletionState(
            request_version=1,
            key="thread-1",
            kind="skill",
            query="rev",
            token_start=4,
            token_end=8,
            skills=skills,
        ),
    )

    async with app.run_test(size=(100, 32)) as pilot:
        base = replace(state, completion=None)
        app.post_message(StateProjected(base, ProjectionHints(changed=frozenset({"focus"}))))
        await pilot.pause(0.05)
        app.query_one("#composer-editor", TextArea).focus()
        app.post_message(StateProjected(state, ProjectionHints(changed=frozenset({"overlay"}))))
        await pilot.pause(0.1)
        assert app.query_one("#completion-popup").display
        assert app.focused is not None and app.focused.id == "completion-list"
        assert app.query_one("#focus-screen").disabled
        completion_list = app.query_one("#completion-list", ListView)
        completion_list.index = 0
        await pilot.press("enter")
        await pilot.pause(0.05)

        applied = next(call.args[0] for call in handled.await_args_list if isinstance(call.args[0], ApplyCompletion))
        assert applied.replacement == "$review"
        assert applied.skill_reference is not None
        assert applied.skill_reference.catalog_id == "c" * 64
        assert applied.skill_reference.item_id == "d" * 64

    await app.controller.close()
