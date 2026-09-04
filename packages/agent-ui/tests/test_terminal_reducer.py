from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from a13n_ui.live import LiveEvent
from a13n_ui.surfaces import (
    AgentSourceView,
    ChildExecutionPage,
    ChildStatusCounts,
    ContinuationSelectionView,
    DeferredRequestView,
    EnvironmentOutcomeView,
    LaunchProjectSelected,
    LaunchProjectUnmatched,
    ProjectSummary,
    RootActivityState,
    RootActivityView,
    RootControlResult,
    RootExecutionView,
    RootOperationStatus,
    RootOperationView,
    RootRunOutcomeView,
    RootRunReceipt,
    ThreadConfigurationView,
    ThreadDetail,
    ThreadFocusSnapshot,
    ThreadSummary,
    TranscriptEntry,
    TranscriptPage,
    TranscriptPart,
    WorkbenchPage,
    WorkbenchThreadView,
)
from a13n_ui.tui.events import (
    CompletionAcknowledged,
    DraftChanged,
    FocusLoaded,
    FollowLatestChanged,
    LiveReceived,
    ReadingAnchorChanged,
    RootControlCompleted,
    RootOperationUpdated,
    RootReceiptAccepted,
    StartupReady,
    StreamPartEvent,
    ToolEvent,
    TranscriptLoaded,
    UnknownLiveEvent,
    WorkbenchLoaded,
    normalize_live_event,
)
from a13n_ui.tui.models import (
    MAX_DRAFTS,
    MAX_TIMELINE_BLOCKS,
    BlockKind,
    BlockStatus,
    ControlMode,
    ReadingAnchor,
    TerminalLifecycle,
    TerminalMode,
    TerminalState,
    ThreadViewState,
    TimelineBlock,
    WorkbenchState,
)
from a13n_ui.tui.reducer import derive_control_mode, ranked_workbench_rows, reduce_terminal

NOW = datetime(2026, 9, 4, tzinfo=UTC)


def _summary(
    thread_id: str = "thread-1",
    *,
    updated_at: datetime = NOW,
    activity: RootActivityView | None = None,
) -> ThreadSummary:
    return ThreadSummary(
        thread_id=thread_id,
        created_at=NOW,
        updated_at=updated_at,
        metadata_version=1,
        archived=False,
        configuration=ThreadConfigurationView(
            version=1,
            project_id="project-main",
            agent_source=AgentSourceView(kind="agent", id="agent-main"),
            environment_profile_id="environment-native",
        ),
        continuation_state="selected",
        root_activity=activity or RootActivityView(state=RootActivityState.inactive),
    )


def _detail(
    thread_id: str = "thread-1",
    *,
    actions: tuple[str, ...] = ("run", "archive"),
) -> ThreadDetail:
    return ThreadDetail(
        thread=_summary(thread_id),
        continuation_id="a" * 64,
        available_actions=actions,
    )


def _snapshot(
    thread_id: str = "thread-1",
    *,
    operation: RootOperationView | None = None,
    epoch: str = "live-1",
    sequence: int = 10,
) -> ThreadFocusSnapshot:
    return ThreadFocusSnapshot(
        epoch=epoch,
        cutover_sequence=sequence,
        thread=_detail(thread_id),
        root_operation=operation,
        children=ChildExecutionPage(executions=(), total=0),
    )


def _operation(
    status: RootOperationStatus,
    *,
    receipt_id: str = "receipt-1",
    thread_id: str = "thread-1",
    retained: bool = True,
) -> RootOperationView:
    terminal = status in {
        RootOperationStatus.completed,
        RootOperationStatus.suspended,
        RootOperationStatus.failed,
        RootOperationStatus.cancelled,
    }
    outcome = None
    if terminal and status in {RootOperationStatus.completed, RootOperationStatus.suspended}:
        outcome = RootRunOutcomeView(
            execution=RootExecutionView(status="completed" if status is RootOperationStatus.completed else "suspended"),
            continuation=ContinuationSelectionView(
                status="selected" if retained else "failed",
                continuation_id="b" * 64 if retained else None,
            ),
            environment=EnvironmentOutcomeView(unchanged=0, published=0, failed=0),
            composition_id="c" * 64,
        )
    return RootOperationView(
        receipt=RootRunReceipt(receipt_id=receipt_id, thread_id=thread_id, submitted_at=NOW),
        status=status,
        run_id=None if status is RootOperationStatus.preparing else "run-1",
        started_at=None if status is RootOperationStatus.preparing else NOW,
        completed_at=NOW if terminal else None,
        outcome=outcome,
        available_actions=("wait", "cancel") if status is RootOperationStatus.preparing else (),
    )


def _focused_state(*, operation: RootOperationView | None = None) -> TerminalState:
    snapshot = _snapshot(operation=operation)
    return TerminalState(
        lifecycle=TerminalLifecycle.READY,
        mode=TerminalMode.FOCUS,
        focused_thread_id="thread-1",
        thread_views=(
            ThreadViewState(
                thread_id="thread-1",
                detail=snapshot.thread,
                snapshot=snapshot,
                root_operation=operation,
                control_mode=derive_control_mode(
                    ThreadViewState(thread_id="thread-1", detail=snapshot.thread, root_operation=operation)
                ),
                epoch=snapshot.epoch,
                last_sequence=snapshot.cutover_sequence,
                projection_version=1,
            ),
        ),
    )


def _workbench_row(
    thread_id: str,
    *,
    updated_at: datetime,
    operation: RootOperationView | None = None,
    pending: bool = False,
    active_children: int = 0,
) -> WorkbenchThreadView:
    from a13n_ui.surfaces import PendingDecisionSummary

    return WorkbenchThreadView(
        thread=_summary(thread_id, updated_at=updated_at),
        project_name="Main",
        agent_name="Main",
        environment_name="Full Control",
        pending_decision=(PendingDecisionSummary(kind="approval", count=1) if pending else None),
        latest_operation=operation,
        children=ChildStatusCounts(
            running=active_children,
            active=active_children,
            unavailable=0,
        ),
    )


def test_startup_uses_selected_project_and_unmatched_falls_back_to_workbench() -> None:
    project = ProjectSummary(
        project_id="project-main",
        name="Main",
        position=0,
        roots=("/workspace",),
    )
    selected = StartupReady(
        launch=LaunchProjectSelected(directory="/workspace", project=project),
        workbench=WorkbenchPage(project_id="project-main", rows=(), total=0),
    )
    ready = reduce_terminal(TerminalState(), selected).state

    assert ready.lifecycle is TerminalLifecycle.READY
    assert ready.mode is TerminalMode.FOCUS
    assert ready.launch_project_id == "project-main"
    assert ready.draft_defaults.project_id == "project-main"

    unmatched = reduce_terminal(
        TerminalState(),
        StartupReady(
            launch=LaunchProjectUnmatched(directory="/tmp"),
            workbench=WorkbenchPage(project_id=None, rows=(), total=0),
        ),
    ).state
    assert unmatched.mode is TerminalMode.WORKBENCH
    assert unmatched.launch_project_id is None


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (None, ControlMode.IDLE),
        (_operation(RootOperationStatus.preparing), ControlMode.PREPARING),
        (_operation(RootOperationStatus.running), ControlMode.RUNNING),
    ],
)
def test_control_modes_follow_authoritative_operation(
    operation: RootOperationView | None,
    expected: ControlMode,
) -> None:
    view = ThreadViewState(thread_id="thread-1", detail=_detail(), root_operation=operation)
    assert derive_control_mode(view) is expected
    assert derive_control_mode(None, draft=True) is ControlMode.DRAFT


def test_pending_requests_and_cancellation_have_distinct_control_modes() -> None:
    detail = _detail().model_copy(
        update={
            "deferred_requests": (
                DeferredRequestView(
                    request_id="request-1",
                    kind="approval",
                    tool_name="shell",
                ),
            )
        }
    )
    assert derive_control_mode(ThreadViewState(thread_id="thread-1", detail=detail)) is ControlMode.AWAITING_DECISION
    cancelling = ThreadViewState(
        thread_id="thread-1",
        detail=detail,
        root_operation=_operation(RootOperationStatus.running),
        cancelling_receipt_id="receipt-1",
    )
    assert derive_control_mode(cancelling) is ControlMode.CANCELLING
    assert derive_control_mode(None) is ControlMode.UNAVAILABLE


def test_focus_and_transcript_reject_stale_versions_and_preserve_prepend_anchor() -> None:
    state = reduce_terminal(
        TerminalState(lifecycle=TerminalLifecycle.READY),
        FocusLoaded(request_version=3, snapshot=_snapshot()),
    ).state
    stale = reduce_terminal(
        state,
        FocusLoaded(request_version=2, snapshot=_snapshot(epoch="live-stale")),
    ).state
    assert stale.thread_view("thread-1").epoch == "live-1"  # type: ignore[union-attr]

    latest = TranscriptPage(
        continuation_id="a" * 64,
        entries=(
            TranscriptEntry(
                position=5,
                message_kind="response",
                parts=(TranscriptPart(kind="assistant", text="latest"),),
            ),
        ),
        total=6,
        next_cursor="older",
    )
    state = reduce_terminal(
        state,
        TranscriptLoaded(request_version=3, thread_id="thread-1", page=latest),
    ).state
    block_id = state.thread_view("thread-1").timeline[0].block_id  # type: ignore[union-attr]
    state = reduce_terminal(
        state,
        ReadingAnchorChanged(thread_id="thread-1", anchor=ReadingAnchor(block_id=block_id, line_offset=2)),
    ).state
    older = TranscriptPage(
        continuation_id="a" * 64,
        entries=(
            TranscriptEntry(
                position=4,
                message_kind="request",
                parts=(TranscriptPart(kind="user", text="older"),),
            ),
        ),
        total=6,
    )
    result = reduce_terminal(
        state,
        TranscriptLoaded(request_version=3, thread_id="thread-1", page=older, prepend=True),
    )

    view = result.state.thread_view("thread-1")
    assert view is not None
    assert [item.source_text for item in view.timeline] == ["older", "latest"]
    assert result.hints.preserve_anchor == ReadingAnchor(block_id=block_id, line_offset=2)


def test_live_text_is_correlated_by_run_and_part_and_duplicate_sequence_is_ignored() -> None:
    state = _focused_state()
    opened = StreamPartEvent(
        epoch="live-1",
        sequence=11,
        thread_id="thread-1",
        run_id="run-1",
        execution_id=None,
        part_id="message-1",
        kind="assistant",
        action="open",
    )
    state = reduce_terminal(state, LiveReceived(opened)).state
    appended = replace(opened, sequence=12, action="append", text="hello")
    state = reduce_terminal(state, LiveReceived(appended)).state
    duplicate = reduce_terminal(state, LiveReceived(appended)).state
    another_run = replace(appended, sequence=13, run_id="run-2", text="other")
    state = reduce_terminal(duplicate, LiveReceived(another_run)).state

    view = state.thread_view("thread-1")
    assert view is not None
    assert len(view.timeline) == 2
    assert view.timeline[0].source_text == "hello"
    assert view.timeline[1].source_text == "other"


def test_live_tool_correlation_and_follow_latest_pending_output() -> None:
    state = reduce_terminal(
        _focused_state(),
        FollowLatestChanged(thread_id="thread-1", enabled=False),
    ).state
    tool = ToolEvent(
        epoch="live-1",
        sequence=11,
        thread_id="thread-1",
        run_id="run-1",
        execution_id=None,
        tool_call_id="call-1",
        action="open",
        tool_name="shell",
    )
    state = reduce_terminal(state, LiveReceived(tool)).state
    state = reduce_terminal(
        state,
        LiveReceived(replace(tool, sequence=12, action="arguments", text='{"cmd":"pwd"}')),
    ).state
    state = reduce_terminal(
        state,
        LiveReceived(replace(tool, sequence=13, action="result", text="/workspace")),
    ).state

    view = state.thread_view("thread-1")
    assert view is not None
    assert len(view.timeline) == 1
    assert view.timeline[0].kind is BlockKind.TOOL
    assert view.timeline[0].status is BlockStatus.CLOSED
    assert view.pending_output > 0
    followed = reduce_terminal(
        state,
        FollowLatestChanged(thread_id="thread-1", enabled=True),
    ).state.thread_view("thread-1")
    assert followed is not None
    assert followed.pending_output == 0


def test_receipt_acceptance_does_not_regress_an_operation_observed_first() -> None:
    running = _operation(RootOperationStatus.running)
    state = _focused_state(operation=running)
    receipt = RootRunReceipt(receipt_id="receipt-1", thread_id="thread-1", submitted_at=NOW)

    state = reduce_terminal(
        state,
        RootReceiptAccepted(
            receipt=receipt,
            draft_key="thread-1",
            submitted_text="steer later",
        ),
    ).state

    view = state.thread_view("thread-1")
    assert view is not None
    assert view.root_operation is running
    assert view.control_mode is ControlMode.RUNNING


def test_receipt_acceptance_clears_draft_and_terminal_unretained_output_is_visible() -> None:
    state = reduce_terminal(
        _focused_state(),
        DraftChanged(key="thread-1", text="hello", cursor=5),
    ).state
    receipt = RootRunReceipt(receipt_id="receipt-1", thread_id="thread-1", submitted_at=NOW)
    state = reduce_terminal(
        state,
        RootReceiptAccepted(receipt=receipt, draft_key="thread-1", submitted_text="hello"),
    ).state
    assert state.draft("thread-1").text == ""  # type: ignore[union-attr]
    assert state.thread_view("thread-1").control_mode is ControlMode.PREPARING  # type: ignore[union-attr]

    state = reduce_terminal(
        state,
        RootOperationUpdated(_operation(RootOperationStatus.completed, retained=False)),
    ).state
    view = state.thread_view("thread-1")
    assert view is not None
    assert view.unretained_output
    assert state.notices[-1].code == "continuation_not_selected"


def test_cancel_acknowledgement_waits_for_terminal_operation_settlement() -> None:
    running = _operation(RootOperationStatus.running)
    state = _focused_state(operation=running)
    state = reduce_terminal(
        state,
        RootControlCompleted(
            result=RootControlResult(receipt_id="receipt-stale", accepted=True),
            action="cancel",
        ),
    ).state
    assert state.thread_view("thread-1").control_mode is ControlMode.RUNNING  # type: ignore[union-attr]

    state = reduce_terminal(
        state,
        RootControlCompleted(
            result=RootControlResult(receipt_id="receipt-1", accepted=True),
            action="cancel",
        ),
    ).state
    assert state.thread_view("thread-1").control_mode is ControlMode.CANCELLING  # type: ignore[union-attr]
    state = reduce_terminal(
        state,
        RootOperationUpdated(_operation(RootOperationStatus.cancelled)),
    ).state
    assert state.thread_view("thread-1").control_mode is ControlMode.IDLE  # type: ignore[union-attr]


def test_workbench_attention_and_completion_acknowledgement_are_local() -> None:
    completed = _operation(
        RootOperationStatus.completed,
        receipt_id="receipt-completed",
        thread_id="thread-completed",
    )
    rows = (
        _workbench_row("thread-idle", updated_at=NOW),
        _workbench_row("thread-completed", updated_at=NOW - timedelta(seconds=1), operation=completed),
        _workbench_row("thread-running", updated_at=NOW - timedelta(seconds=2), active_children=1),
        _workbench_row("thread-decision", updated_at=NOW - timedelta(seconds=3), pending=True),
    )
    page = WorkbenchPage(project_id=None, rows=rows, total=4)
    state = TerminalState(
        lifecycle=TerminalLifecycle.READY,
        mode=TerminalMode.WORKBENCH,
        workbench=WorkbenchState(),
    )
    state = reduce_terminal(state, WorkbenchLoaded(request_version=1, page=page)).state
    assert [row.thread.thread_id for row in ranked_workbench_rows(state.workbench)] == [
        "thread-decision",
        "thread-completed",
        "thread-running",
        "thread-idle",
    ]
    state = reduce_terminal(state, CompletionAcknowledged(receipt_id="receipt-completed")).state
    assert [row.thread.thread_id for row in ranked_workbench_rows(state.workbench)] == [
        "thread-decision",
        "thread-running",
        "thread-idle",
        "thread-completed",
    ]


def test_draft_cache_and_timeline_are_bounded_without_evicting_open_or_anchor_blocks() -> None:
    state = TerminalState(lifecycle=TerminalLifecycle.READY)
    for index in range(MAX_DRAFTS + 5):
        state = reduce_terminal(
            state,
            DraftChanged(key=f"thread-{index}", text=str(index), cursor=1),
        ).state
    assert len(state.drafts) == MAX_DRAFTS
    assert state.draft("thread-0") is None

    blocks = tuple(
        TimelineBlock(
            block_id=f"block-{index}",
            thread_id="thread-1",
            kind=BlockKind.ASSISTANT,
            status=BlockStatus.RUNNING if index == 0 else BlockStatus.CLOSED,
        )
        for index in range(MAX_TIMELINE_BLOCKS + 10)
    )
    view = ThreadViewState(
        thread_id="thread-1",
        detail=_detail(),
        epoch="live-1",
        last_sequence=10,
        timeline=blocks,
        reading_anchor=ReadingAnchor(block_id="block-1"),
    )
    state = TerminalState(
        lifecycle=TerminalLifecycle.READY,
        focused_thread_id="thread-1",
        thread_views=(view,),
    )
    state = reduce_terminal(
        state,
        LiveReceived(
            UnknownLiveEvent(
                epoch="live-1",
                sequence=11,
                thread_id="thread-1",
                run_id="run-1",
                execution_id=None,
                event_type="CUSTOM",
                summary="custom",
            )
        ),
    ).state
    timeline = state.thread_view("thread-1").timeline  # type: ignore[union-attr]
    assert len(timeline) == MAX_TIMELINE_BLOCKS
    assert any(item.block_id == "block-0" for item in timeline)
    assert any(item.block_id == "block-1" for item in timeline)


def test_live_normalization_handles_standard_and_unknown_events() -> None:
    text = normalize_live_event(
        LiveEvent(
            epoch="live-1",
            sequence=1,
            run_kind="root",
            root_thread_id="thread-1",
            thread_id="thread-1",
            run_id="run-1",
            event_type="TEXT_MESSAGE_CONTENT",
            payload={"type": "TEXT_MESSAGE_CONTENT", "message_id": "m1", "delta": "hi"},
            payload_omitted=False,
        )
    )
    assert isinstance(text, StreamPartEvent)
    assert text.part_id == "m1"
    assert text.text == "hi"

    unknown = normalize_live_event(
        LiveEvent(
            epoch="live-1",
            sequence=2,
            run_kind="root",
            root_thread_id="thread-1",
            thread_id="thread-1",
            run_id="run-1",
            event_type="CUSTOM",
            payload={"type": "CUSTOM", "name": "vendor.event", "value": {"ok": True}},
            payload_omitted=False,
        )
    )
    assert isinstance(unknown, UnknownLiveEvent)
    assert "vendor.event" in unknown.summary
