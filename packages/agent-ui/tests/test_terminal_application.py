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
    ChildStatusCounts,
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
    ThreadActivityPage,
    ThreadActivityView,
    ThreadConfigurationView,
    ThreadDetail,
    ThreadFocusSnapshot,
    ThreadSelectorCatalog,
    ThreadSummary,
)
from a13n_ui.tui.application import AgentUiTerminalApp, StateProjected
from a13n_ui.tui.intents import (
    ApplyCompletion,
    CloseOverlay,
    ExecuteCommand,
    ExitTerminal,
    InsertSkillReference,
    OpenOverlay,
    SelectConfigurationResource,
    SetReadingAnchor,
    SetThreadFilter,
    SubmitComposer,
    SubmitDecisionSession,
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
    ReadingAnchor,
    ReviewState,
    TerminalLifecycle,
    TerminalState,
    ThreadActivityState,
    ThreadViewState,
    TimelineBlock,
)
from a13n_ui.tui.scheduler import ProjectionScheduler
from a13n_ui.tui.widgets.blocks import TimelineBlockWidget
from a13n_ui.tui.widgets.timeline import TimelineScroll
from textual.widgets import ListView, Markdown, Static, TextArea


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
async def test_failed_terminal_ctrl_c_exits_instead_of_cancelling_stale_work(tmp_path: Path) -> None:
    app, _release = _blocked_terminal_app(tmp_path)
    handled = AsyncMock()
    app.controller.handle = handled
    state = _focused_state()
    failed_view = replace(state.thread_views[0], control_mode=ControlMode.RUNNING)
    failed_state = replace(
        state,
        lifecycle=TerminalLifecycle.FAILED,
        thread_views=(failed_view,),
    )

    async with app.run_test(size=(100, 32)) as pilot:
        app.post_message(StateProjected(failed_state, ProjectionHints(changed=frozenset({"lifecycle"}))))
        await pilot.pause(0.05)
        await pilot.press("ctrl+c")
        await pilot.pause(0.02)
        assert isinstance(handled.await_args_list[-1].args[0], ExitTerminal)

    await app.controller.close()


@pytest.mark.anyio
async def test_surface_and_controller_failures_remain_at_terminal_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _release = _blocked_terminal_app(tmp_path)

    async with app.run_test(size=(100, 32)) as pilot:
        focus_screen = app.query_one("#focus-screen")
        monkeypatch.setattr(
            focus_screen,
            "project",
            AsyncMock(side_effect=RuntimeError("render failed")),
        )
        app.post_message(StateProjected(_focused_state(), ProjectionHints(changed=frozenset({"focus"}))))
        await pilot.pause(0.05)
        assert "could not be rendered safely" in str(app.query_one("#focus-draft-intro", Static).render())

        async def fail_handle(intent: object) -> None:
            del intent
            raise RuntimeError("controller failed")

        failed = AsyncMock()
        monkeypatch.setattr(app.controller, "handle", fail_handle)
        monkeypatch.setattr(app.controller, "fail", failed)
        await pilot.press("ctrl+p")
        await pilot.pause(0.05)
        failed.assert_awaited_once()
        assert isinstance(failed.await_args.args[0], RuntimeError)

    await app.controller.close()


@pytest.mark.anyio
@pytest.mark.parametrize("prepend", [False, True])
async def test_timeline_reports_reading_anchor_and_preserves_it_on_projection(tmp_path: Path, prepend: bool) -> None:
    app, _release = _blocked_terminal_app(tmp_path)
    handled = AsyncMock()
    app.controller.handle = handled
    blocks = tuple(
        TimelineBlock(
            block_id=f"retained:{index}",
            thread_id="thread-1",
            kind=BlockKind.USER,
            status=BlockStatus.CLOSED,
            source_text="\n".join(f"line {line}" for line in range(5)),
        )
        for index in range(12)
    )
    state = _focused_state(timeline=blocks)

    async with app.run_test(size=(60, 24)) as pilot:
        app.post_message(StateProjected(state, ProjectionHints(changed=frozenset({"focus"}))))
        await pilot.pause(0.1)
        scroll = app.query_one(TimelineScroll)
        assert scroll.max_scroll_y > 4
        scroll.scroll_to(y=4, animate=False, immediate=True)
        await pilot.pause(0.05)

        intent = next(
            call.args[0] for call in reversed(handled.await_args_list) if isinstance(call.args[0], SetReadingAnchor)
        )
        assert intent.thread_id == "thread-1"
        assert intent.block_id in {block.block_id for block in blocks}

        anchor = ReadingAnchor(intent.block_id, intent.line_offset)
        timeline = (replace(blocks[0], block_id="older"), *blocks) if prepend else blocks
        view = replace(state.thread_views[0], reading_anchor=anchor, timeline=timeline, follow_latest=False)
        projected = replace(state, thread_views=(view,))
        scroll.scroll_end(animate=False, immediate=True)
        app.post_message(
            StateProjected(
                projected,
                ProjectionHints(changed=frozenset({"focus"}), preserve_anchor=anchor),
            )
        )
        await pilot.pause(0.05)
        assert scroll.scroll_y < scroll.max_scroll_y
        assert app.terminal_state.thread_view("thread-1").reading_anchor == anchor
        anchored_widget = next(
            widget for widget in scroll.query(TimelineBlockWidget) if widget.block.block_id == anchor.block_id
        )
        assert abs(scroll.scroll_y - (anchored_widget.virtual_region.y + anchor.line_offset)) <= 1

        await pilot.resize_terminal(100, 28)
        await pilot.pause(0.05)
        anchored_widget = next(
            widget for widget in scroll.query(TimelineBlockWidget) if widget.block.block_id == anchor.block_id
        )
        expected_y = min(
            scroll.max_scroll_y,
            anchored_widget.virtual_region.y + anchor.line_offset,
        )
        assert abs(scroll.scroll_y - expected_y) <= 1

        await pilot.resize_terminal(60, 24)
        await pilot.pause(0.05)
        expected_y = min(
            scroll.max_scroll_y,
            anchored_widget.virtual_region.y + anchor.line_offset,
        )
        assert abs(scroll.scroll_y - expected_y) <= 1

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


def _thread_activity_state() -> TerminalState:
    focused = _focused_state()
    thread = focused.thread_views[0].detail
    assert thread is not None
    row = ThreadActivityView(
        thread=thread.thread,
        project_name="Main",
        agent_name="Agent",
        environment_name="Full Control",
        latest_activity=ActivitySummary(kind="assistant", text="Finished inspection", occurred_at=NOW),
        available_actions=("open", "archive"),
    )
    return replace(
        focused,
        project_filter_id="project-main",
        thread_activity=ThreadActivityState(
            page=ThreadActivityPage(project_id="project-main", rows=(row,), total=1),
            projection_version=1,
        ),
    )


@pytest.mark.anyio
async def test_medium_and_narrow_use_one_pane_preview_and_inspector_drill_down(tmp_path: Path) -> None:
    app, _release = _blocked_terminal_app(tmp_path)
    handled = AsyncMock()
    app.controller.handle = handled
    async with app.run_test(size=(130, 36)) as pilot:
        focus_state = _focused_state()
        app.post_message(StateProjected(focus_state, ProjectionHints(changed=frozenset({"focus"}))))
        await pilot.pause(0.05)
        assert app.query_one("#focus-inspector").display
        assert not app.query_one("#focus-inspect").display

        await pilot.resize_terminal(100, 32)
        await pilot.pause(0.05)
        assert not app.query_one("#focus-inspector").display
        assert app.query_one("#focus-inspect").display
        handled.reset_mock()
        await pilot.click("#focus-inspect")
        await pilot.pause(0.02)
        inspect_intent = handled.await_args_list[-1].args[0]
        assert isinstance(inspect_intent, OpenOverlay)
        assert inspect_intent.kind == "inspector"

        inspector_state = replace(
            focus_state,
            overlays=(OverlayState(kind="inspector", context_key="thread-1"),),
        )
        app.post_message(StateProjected(inspector_state, ProjectionHints(changed=frozenset({"overlay"}))))
        await pilot.resize_terminal(60, 28)
        await pilot.pause(0.05)
        inspector_text = str(app.query_one("#overlay-body", Static).render())
        assert "CONTEXT" in inspector_text
        assert "CHILDREN" in inspector_text
        assert app.terminal_state.overlays[-1].kind == "inspector"

    await app.controller.close()


@pytest.mark.anyio
async def test_exit_overlay_explains_active_work_and_requires_explicit_action(tmp_path: Path) -> None:
    app, _release = _blocked_terminal_app(tmp_path)
    handled = AsyncMock()
    app.controller.handle = handled
    state = _thread_activity_state()
    row = state.thread_activity.rows[0]
    active_thread = row.thread.model_copy(
        update={
            "root_activity": RootActivityView(
                state=RootActivityState.running,
                receipt_id="receipt-1",
                run_id="run-1",
                available_actions=("wait", "steer", "cancel"),
            )
        }
    )
    active_row = row.model_copy(
        update={
            "thread": active_thread,
            "children": ChildStatusCounts(running=1, active=1),
        }
    )
    active_state = replace(
        state,
        thread_activity=replace(
            state.thread_activity,
            page=ThreadActivityPage(project_id="project-main", rows=(active_row,), total=1),
        ),
        overlays=(
            OverlayState(
                kind="exit",
                active_root_operations=1,
                active_child_executions=1,
            ),
        ),
    )

    async with app.run_test(size=(60, 28)) as pilot:
        app.post_message(StateProjected(active_state, ProjectionHints(changed=frozenset({"overlay"}))))
        await pilot.pause(0.05)

        body = str(app.query_one("#overlay-body", Static).render())
        assert "Active root operations: 1" in body
        assert "Active child executions: 1" in body
        assert "will not continue" in body
        assert app.query_one("#overlay-close").has_focus

        await pilot.click("#overlay-confirm-exit")
        await pilot.pause(0.02)
        intent = handled.await_args_list[-1].args[0]
        assert isinstance(intent, ExitTerminal)
        assert intent.confirmed

        await pilot.click("#overlay-close")
        await pilot.pause(0.02)
        assert isinstance(handled.await_args_list[-1].args[0], CloseOverlay)

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
            _thread_activity_state(),
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
            isinstance(call.args[0], SetThreadFilter) and call.args[0].project_id == "project-main"
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
        assert app.focused is not None and app.focused.id == "composer-editor"
        assert not app.query_one("#focus-screen").disabled
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


@pytest.mark.anyio
async def test_thread_picker_has_no_second_composer_or_inline_prompt(tmp_path: Path) -> None:
    from a13n_ui.tui.intents import OpenFocus, SearchThreadPicker
    from textual.widgets import Input

    app, _release = _blocked_terminal_app(tmp_path)
    handled = AsyncMock()
    app.controller.handle = handled
    state = _thread_activity_state()
    page = state.thread_activity.page
    assert page is not None
    state = replace(
        state,
        thread_picker=page,
        overlays=(OverlayState(kind="threads", context_key="thread-1"),),
    )
    async with app.run_test(size=(100, 32)) as pilot:
        app.post_message(StateProjected(state, ProjectionHints(changed=frozenset({"overlay"}))))
        await pilot.pause(0.1)
        assert len(app.query("#composer-editor")) == 1
        assert len(app.query("#workbench-screen")) == 0
        app.query_one("#overlay-search", Input).value = "failure"
        await pilot.pause(0.3)
        assert any(
            isinstance(c.args[0], SearchThreadPicker) and c.args[0].query == "failure" for c in handled.await_args_list
        )
        app.query_one("#overlay-list", ListView).focus()
        await pilot.press("enter")
        await pilot.pause(0.05)
        assert any(isinstance(c.args[0], OpenFocus) for c in handled.await_args_list)
        await pilot.resize_terminal(60, 28)
        await pilot.pause(0.05)
        assert app.has_class("width-narrow")
        assert app.terminal_state.focused_thread_id == "thread-1"
        assert len(app.query("#composer-editor")) == 1
    await app.controller.close()
