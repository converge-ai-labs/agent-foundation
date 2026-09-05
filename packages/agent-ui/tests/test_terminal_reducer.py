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
    ThreadActivityPage,
    ThreadActivityView,
    ThreadConfigurationView,
    ThreadDetail,
    ThreadFocusSnapshot,
    ThreadSummary,
    TranscriptEntry,
    TranscriptPage,
    TranscriptPart,
)
from a13n_ui.tui.events import (
    ClosingStarted,
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
    ThreadActivityLoaded,
    ToolEvent,
    TranscriptLoaded,
    UnknownLiveEvent,
    normalize_live_event,
)
from a13n_ui.tui.models import (
    MAX_DRAFTS,
    MAX_TIMELINE_BLOCKS,
    BlockKind,
    BlockStatus,
    ControlMode,
    OverlayState,
    ReadingAnchor,
    TerminalLifecycle,
    TerminalState,
    ThreadActivityState,
    ThreadViewState,
    TimelineBlock,
)
from a13n_ui.tui.reducer import derive_control_mode, reduce_terminal

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


def _thread_activity_row(
    thread_id: str,
    *,
    updated_at: datetime,
    operation: RootOperationView | None = None,
    pending: bool = False,
    active_children: int = 0,
    failed_children: int = 0,
    lost_children: int = 0,
) -> ThreadActivityView:
    from a13n_ui.surfaces import PendingDecisionSummary

    return ThreadActivityView(
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
            failed=failed_children,
            lost=lost_children,
        ),
    )


def test_retained_page_replaces_only_correlated_published_live_output() -> None:
    state = reduce_terminal(TerminalState(), FocusLoaded(1, _snapshot())).state
    receipt = _operation(RootOperationStatus.preparing).receipt
    state = reduce_terminal(state, RootReceiptAccepted(receipt, "thread-1", "hello")).state
    state = reduce_terminal(
        state,
        LiveReceived(
            StreamPartEvent(
                epoch="live-1",
                sequence=11,
                thread_id="thread-1",
                run_id="run-1",
                execution_id=None,
                part_id="part-1",
                kind="assistant",
                action="append",
                text="answer",
            )
        ),
    ).state
    state = reduce_terminal(state, RootOperationUpdated(_operation(RootOperationStatus.completed))).state
    snapshot = _snapshot(operation=_operation(RootOperationStatus.completed)).model_copy(
        update={
            "thread": _detail().model_copy(update={"continuation_id": "b" * 64}),
        }
    )
    state = reduce_terminal(state, FocusLoaded(2, snapshot)).state
    page = TranscriptPage(
        continuation_id="b" * 64,
        total=2,
        entries=(
            TranscriptEntry(position=0, message_kind="request", parts=(TranscriptPart(kind="user", text="hello"),)),
            TranscriptEntry(
                position=1, message_kind="response", parts=(TranscriptPart(kind="assistant", text="answer"),)
            ),
        ),
    )
    state = reduce_terminal(state, TranscriptLoaded(2, "thread-1", page)).state
    blocks = state.thread_view("thread-1").timeline
    assert [block.source_text for block in blocks] == ["hello", "answer"]
    assert not any(block.provisional for block in blocks)


@pytest.mark.parametrize("after_transcript", [False, True])
def test_buffered_output_after_terminal_receipt_is_reconciled(after_transcript: bool) -> None:
    state = reduce_terminal(TerminalState(), FocusLoaded(1, _snapshot())).state
    state = reduce_terminal(state, RootOperationUpdated(_operation(RootOperationStatus.completed))).state
    late = LiveReceived(
        StreamPartEvent(
            epoch="live-1",
            sequence=11,
            thread_id="thread-1",
            run_id="run-1",
            execution_id=None,
            part_id="late",
            kind="assistant",
            action="append",
            text="answer",
        )
    )
    if not after_transcript:
        state = reduce_terminal(state, late).state
        assert state.thread_view("thread-1").timeline[0].status is BlockStatus.CLOSED
    snapshot = _snapshot().model_copy(
        update={
            "thread": _detail().model_copy(update={"continuation_id": "b" * 64}),
        }
    )
    state = reduce_terminal(state, FocusLoaded(2, snapshot)).state
    page = TranscriptPage(
        continuation_id="b" * 64,
        total=1,
        entries=(
            TranscriptEntry(
                position=0, message_kind="response", parts=(TranscriptPart(kind="assistant", text="answer"),)
            ),
        ),
    )
    state = reduce_terminal(state, TranscriptLoaded(2, "thread-1", page)).state
    if after_transcript:
        state = reduce_terminal(state, late).state
    assert [block.source_text for block in state.thread_view("thread-1").timeline] == ["answer"]
    assert not state.thread_view("thread-1").timeline[0].provisional


def test_new_run_task_versions_do_not_inherit_unretained_task_state() -> None:
    from a13n_ui.surfaces import TaskView
    from a13n_ui.tui.events import TaskChangedEvent

    first = _operation(RootOperationStatus.running)
    state = reduce_terminal(TerminalState(), FocusLoaded(1, _snapshot(operation=first))).state
    task = TaskView(task_id="task-1", version=2, subject="Review", status="completed")
    event = TaskChangedEvent(
        epoch="live-1",
        sequence=11,
        thread_id="thread-1",
        run_id="run-1",
        execution_id=None,
        task_state_version=2,
        task=task,
    )
    state = reduce_terminal(state, LiveReceived(event)).state
    state = reduce_terminal(
        state, RootOperationUpdated(_operation(RootOperationStatus.completed, retained=False))
    ).state
    second = _operation(RootOperationStatus.running, receipt_id="receipt-2").model_copy(update={"run_id": "run-2"})
    state = reduce_terminal(state, FocusLoaded(2, _snapshot(operation=second, sequence=11))).state
    state = reduce_terminal(
        state,
        LiveReceived(
            replace(
                event,
                sequence=12,
                run_id="run-2",
                task_state_version=1,
                task=task.model_copy(update={"version": 1, "status": "in_progress"}),
            )
        ),
    ).state
    view = state.thread_view("thread-1")
    assert view.tasks_run_id == "run-2"
    assert view.tasks.version == 1
    assert view.tasks.tasks[0].status == "in_progress"
    assert [block.status for block in view.timeline if block.kind is BlockKind.TASK] == [BlockStatus.RUNNING]


@pytest.mark.parametrize("created, expected_total", [(False, 3), (True, 4)])
def test_task_delta_distinguishes_new_tasks_from_omitted_existing_tasks(created: bool, expected_total: int) -> None:
    from a13n_ui.surfaces import TaskPage, TaskView
    from a13n_ui.tui.events import TaskChangedEvent

    snapshot = _snapshot(operation=_operation(RootOperationStatus.running)).model_copy(
        update={
            "tasks": TaskPage(version=1, total=3, omitted=3),
        }
    )
    state = reduce_terminal(TerminalState(), FocusLoaded(1, snapshot)).state
    event = TaskChangedEvent(
        epoch="live-1",
        sequence=11,
        thread_id="thread-1",
        run_id="run-1",
        execution_id=None,
        task_state_version=2,
        created=created,
        task=TaskView(task_id="task-1", version=1, subject="Review", status="in_progress"),
    )
    state = reduce_terminal(state, LiveReceived(event)).state
    assert state.thread_view("thread-1").tasks.total == expected_total
    assert state.thread_view("thread-1").tasks.omitted == expected_total - 1


def test_terminal_child_snapshot_closes_exact_partial_output() -> None:
    from a13n_ui.surfaces import ChildActivityView, ChildExecutionView

    state = reduce_terminal(TerminalState(), FocusLoaded(1, _snapshot())).state
    part = StreamPartEvent(
        epoch="live-1",
        sequence=11,
        thread_id="thread-child",
        run_id="run-child",
        execution_id="execution-1",
        root_thread_id="thread-1",
        parent_thread_id="thread-1",
        part_id="part",
        kind="assistant",
        action="append",
        text="partial",
    )
    state = reduce_terminal(state, LiveReceived(part)).state
    state = reduce_terminal(state, LiveReceived(replace(part, sequence=12, run_id="run-other"))).state
    child = ChildExecutionView(
        execution_id="execution-1",
        root_thread_id="thread-1",
        parent_thread_id="thread-1",
        child_thread_id="thread-child",
        child_run_id="run-child",
        segment_index=0,
        composition_id="c" * 64,
        subagent_name="reviewer",
        child_definition_id="reviewer",
        persisted_status="cancelled",
        local_status="unavailable",
        resumable=False,
        activity=ChildActivityView(sequence=12),
        created_at=NOW,
        updated_at=NOW,
        completed_at=NOW,
    )
    snapshot = _snapshot(sequence=12).model_copy(update={"children": ChildExecutionPage(executions=(child,), total=1)})
    state = reduce_terminal(state, FocusLoaded(2, snapshot)).state
    state = reduce_terminal(state, LiveReceived(replace(part, sequence=13, part_id="buffered"))).state
    blocks = [block for block in state.thread_view("thread-1").timeline if block.kind is BlockKind.ASSISTANT]
    assert [block.status for block in blocks] == [BlockStatus.CANCELLED, BlockStatus.RUNNING, BlockStatus.CANCELLED]


def test_history_prepend_evicts_newer_rows_and_keeps_a_latest_reload_path() -> None:
    def page(start: int, end: int, cursor: str | None) -> TranscriptPage:
        return TranscriptPage(
            continuation_id="a" * 64,
            total=550,
            next_cursor=cursor,
            entries=tuple(
                TranscriptEntry(
                    position=index, message_kind="response", parts=(TranscriptPart(kind="assistant", text=str(index)),)
                )
                for index in range(start, end)
            ),
        )

    state = reduce_terminal(TerminalState(), FocusLoaded(1, _snapshot())).state
    state = reduce_terminal(state, TranscriptLoaded(1, "thread-1", page(50, 550, "older"))).state
    view = state.thread_view("thread-1")
    anchor = ReadingAnchor(view.timeline[0].block_id)
    state = reduce_terminal(state, ReadingAnchorChanged("thread-1", anchor)).state
    state = reduce_terminal(state, TranscriptLoaded(1, "thread-1", page(0, 50, None), prepend=True)).state
    view = state.thread_view("thread-1")
    assert len(view.timeline) == MAX_TIMELINE_BLOCKS
    assert view.timeline[0].retained_position == 0
    assert view.reading_anchor == anchor
    assert view.older_cursor is None
    assert view.newer_history_omitted
    state = reduce_terminal(state, TranscriptLoaded(1, "thread-1", page(500, 550, "older"))).state
    assert not state.thread_view("thread-1").newer_history_omitted
    assert state.thread_view("thread-1").timeline[-1].retained_position == 549


def test_child_live_output_routes_to_root_without_losing_source_identity() -> None:
    state = reduce_terminal(TerminalState(), FocusLoaded(1, _snapshot())).state
    raw = LiveEvent(
        epoch="live-1",
        sequence=11,
        run_kind="child",
        root_thread_id="thread-1",
        parent_thread_id="thread-1",
        thread_id="thread-child",
        run_id="run-child",
        execution_id="execution-1",
        event_type="TEXT_MESSAGE_CONTENT",
        payload={"message_id": "part-1", "delta": "child answer"},
        payload_omitted=False,
    )
    state = reduce_terminal(state, LiveReceived(normalize_live_event(raw))).state
    block = state.thread_view("thread-1").timeline[-1]
    assert block.source_text == "child answer"
    assert block.root_thread_id == "thread-1"
    assert block.thread_id == "thread-child"
    assert block.execution_id == "execution-1"
    assert state.thread_view("thread-child") is None


def test_known_task_deltas_update_root_tasks_and_survive_same_continuation_refresh() -> None:
    running = _operation(RootOperationStatus.running)
    state = reduce_terminal(TerminalState(), FocusLoaded(1, _snapshot(operation=running))).state
    for sequence, version, status in ((11, 1, "in_progress"), (12, 2, "completed"), (13, 1, "pending")):
        raw = LiveEvent(
            epoch="live-1",
            sequence=sequence,
            run_kind="root",
            root_thread_id="thread-1",
            thread_id="thread-1",
            run_id="run-1",
            event_type="CUSTOM",
            payload_omitted=False,
            payload={
                "name": "a13n.harness.state",
                "value": {
                    "event": {
                        "kind": "state",
                        "payload": {
                            "type": "task_changed",
                            "task_state_version": version,
                            "task": {"id": "task-1", "version": version, "subject": "Review", "status": status},
                        },
                    }
                },
            },
        )
        state = reduce_terminal(state, LiveReceived(normalize_live_event(raw))).state
    state = reduce_terminal(state, FocusLoaded(2, _snapshot(operation=running, sequence=13))).state
    view = state.thread_view("thread-1")
    assert view.tasks.version == 2
    assert view.tasks.total == 1
    assert view.tasks.tasks[0].status == "completed"
    assert view.timeline[0].kind is BlockKind.TASK
    assert view.timeline[0].status is BlockStatus.CLOSED


def test_closing_clears_transient_surfaces_without_changing_domain_state() -> None:
    state = TerminalState(
        lifecycle=TerminalLifecycle.READY,
        focused_thread_id="thread-1",
        overlays=(OverlayState(kind="status"),),
    )

    result = reduce_terminal(state, ClosingStarted())

    assert result.state.lifecycle is TerminalLifecycle.CLOSING
    assert result.state.focused_thread_id == "thread-1"
    assert result.state.overlays == ()
    assert result.hints.changed == frozenset({"lifecycle", "overlay"})


def test_startup_uses_selected_project_and_unmatched_falls_back_to_thread_activity() -> None:
    project = ProjectSummary(
        project_id="project-main",
        name="Main",
        position=0,
        roots=("/workspace",),
    )
    selected = StartupReady(
        launch=LaunchProjectSelected(directory="/workspace", project=project),
        thread_activity=ThreadActivityPage(project_id="project-main", rows=(), total=0),
    )
    ready = reduce_terminal(TerminalState(), selected).state

    assert ready.lifecycle is TerminalLifecycle.READY
    assert ready.focused_thread_id is None
    assert ready.launch_project_id == "project-main"
    assert ready.draft_defaults.project_id == "project-main"

    unmatched = reduce_terminal(
        TerminalState(),
        StartupReady(
            launch=LaunchProjectUnmatched(directory="/tmp"),
            thread_activity=ThreadActivityPage(project_id=None, rows=(), total=0),
        ),
    ).state
    assert unmatched.focused_thread_id is None
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


def test_thread_activity_preserves_projection_recency_without_attention_ranking() -> None:
    completed = _operation(
        RootOperationStatus.completed,
        receipt_id="receipt-completed",
        thread_id="thread-completed",
    )
    rows = (
        _thread_activity_row("thread-idle", updated_at=NOW),
        _thread_activity_row("thread-completed", updated_at=NOW - timedelta(seconds=1), operation=completed),
        _thread_activity_row("thread-running", updated_at=NOW - timedelta(seconds=2), active_children=1),
        _thread_activity_row("thread-decision", updated_at=NOW - timedelta(seconds=3), pending=True),
        _thread_activity_row("thread-failed-child", updated_at=NOW - timedelta(seconds=4), failed_children=1),
    )
    page = ThreadActivityPage(project_id=None, rows=rows, total=5)
    state = TerminalState(
        lifecycle=TerminalLifecycle.READY,
        thread_activity=ThreadActivityState(),
    )
    state = reduce_terminal(state, ThreadActivityLoaded(request_version=1, page=page)).state
    assert state.thread_activity.rows == rows
    assert state.focused_thread_id is None


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
