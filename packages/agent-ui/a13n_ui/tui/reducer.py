"""Pure deterministic reducer for terminal semantic state."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Literal

from a13n_ui.surfaces import (
    LaunchProjectSelected,
    RootOperationStatus,
    RootOperationView,
    SkillReference,
    ThreadDetail,
    TranscriptPage,
    WorkbenchPage,
    WorkbenchThreadView,
)
from a13n_ui.tui.events import (
    ClosingStarted,
    CompletionAcknowledged,
    DraftChanged,
    DraftDefaultsChanged,
    DraftRestored,
    FocusLoaded,
    FollowLatestChanged,
    LiveReceived,
    LiveUnavailable,
    OperationFailed,
    OverlayClosed,
    OverlayOpened,
    ReadingAnchorChanged,
    RootControlCompleted,
    RootOperationUpdated,
    RootReceiptAccepted,
    RouteChanged,
    RunHintEvent,
    StartupFailed,
    StartupReady,
    StartupStarted,
    StreamPartEvent,
    TerminalEvent,
    TimelineSelectionChanged,
    ToolEvent,
    TranscriptLoaded,
    UnknownLiveEvent,
    WorkbenchLoaded,
    WorkbenchSelectionChanged,
)
from a13n_ui.tui.models import (
    MAX_BLOCK_TEXT,
    MAX_DRAFTS,
    MAX_NOTICES,
    MAX_TIMELINE_BLOCKS,
    BlockKind,
    BlockStatus,
    ControlMode,
    DraftState,
    ProjectionHints,
    Reduction,
    TerminalLifecycle,
    TerminalMode,
    TerminalNotice,
    TerminalState,
    ThreadViewState,
    TimelineBlock,
    WorkbenchState,
)

_EMPTY_HINTS = ProjectionHints(changed=frozenset())
_TERMINAL_OPERATION_STATUSES = frozenset(
    {
        RootOperationStatus.completed,
        RootOperationStatus.suspended,
        RootOperationStatus.failed,
        RootOperationStatus.cancelled,
    }
)


def reduce_terminal(state: TerminalState, event: TerminalEvent) -> Reduction:
    """Apply one explicit fact without performing I/O or reading wall time."""

    if isinstance(event, StartupStarted):
        return _result(
            replace(state, lifecycle=TerminalLifecycle.STARTING, notices=()),
            "lifecycle",
            "notice",
        )

    if isinstance(event, StartupReady):
        launch_project_id = event.launch.project.project_id if isinstance(event.launch, LaunchProjectSelected) else None
        mode = TerminalMode.WORKBENCH if event.open_workbench or launch_project_id is None else TerminalMode.FOCUS
        focused = event.explicit_thread_id
        if focused is not None:
            mode = TerminalMode.FOCUS
        defaults = state.draft_defaults.model_copy(
            update={
                "project_id": state.draft_defaults.project_id or launch_project_id,
            }
        )
        selected = event.workbench.rows[0].thread.thread_id if event.workbench.rows else None
        next_state = replace(
            state,
            lifecycle=TerminalLifecycle.READY,
            mode=mode,
            launch_resolution=event.launch,
            launch_project_id=launch_project_id,
            project_filter_id=event.workbench.project_id,
            focused_thread_id=focused,
            draft_defaults=defaults,
            workbench=WorkbenchState(
                page=_ranked_page(event.workbench, frozenset()),
                selected_thread_id=selected,
                projection_version=1,
            ),
        )
        return _result(next_state, "lifecycle", "route", "workbench", "focus")

    if isinstance(event, StartupFailed):
        next_state = _notice(
            replace(state, lifecycle=TerminalLifecycle.FAILED),
            severity="error",
            message=event.failure.message,
            code=event.failure.code,
        )
        return _result(next_state, "lifecycle", "notice")

    if isinstance(event, ClosingStarted):
        return _result(replace(state, lifecycle=TerminalLifecycle.CLOSING), "lifecycle")

    if isinstance(event, WorkbenchLoaded):
        if event.request_version < state.workbench.projection_version:
            return Reduction(state, _EMPTY_HINTS)
        ranked = _ranked_page(event.page, state.workbench.acknowledged_receipts)
        selected = state.workbench.selected_thread_id
        visible = {row.thread.thread_id for row in ranked.rows}
        if selected not in visible:
            selected = ranked.rows[0].thread.thread_id if ranked.rows else None
        workbench = replace(
            state.workbench,
            page=ranked,
            query=event.query,
            selected_thread_id=selected,
            projection_version=event.request_version,
        )
        return _result(replace(state, workbench=workbench, project_filter_id=event.page.project_id), "workbench")

    if isinstance(event, FocusLoaded):
        thread_id = event.snapshot.thread.thread.thread_id
        current = state.thread_view(thread_id)
        if current is not None and event.request_version < current.projection_version:
            return Reduction(state, _EMPTY_HINTS)
        timeline = () if current is None else current.timeline
        if current is not None and current.epoch not in {None, event.snapshot.epoch}:
            timeline = tuple(block for block in timeline if not block.provisional)
        view = ThreadViewState(
            thread_id=thread_id,
            detail=event.snapshot.thread,
            snapshot=event.snapshot,
            root_operation=event.snapshot.root_operation,
            tasks=event.snapshot.tasks,
            decisions=event.decisions,
            timeline=timeline,
            control_mode=_control_mode(
                event.snapshot.thread,
                event.snapshot.root_operation,
                event.decisions is not None,
            ),
            epoch=event.snapshot.epoch,
            last_sequence=event.snapshot.cutover_sequence,
            projection_version=event.request_version,
            transcript_continuation_id=(None if current is None else current.transcript_continuation_id),
            older_cursor=None if current is None else current.older_cursor,
            follow_latest=True if current is None else current.follow_latest,
            pending_output=0 if current is None else current.pending_output,
            selected_block_id=None if current is None else current.selected_block_id,
            reading_anchor=None if current is None else current.reading_anchor,
            cancelling_receipt_id=None if current is None else current.cancelling_receipt_id,
            unretained_output=False if current is None else current.unretained_output,
        )
        next_state = _with_view(
            replace(
                state,
                focused_thread_id=thread_id,
                previous_focused_thread_id=thread_id,
                mode=TerminalMode.FOCUS,
            ),
            view,
        )
        return _result(next_state, "route", "focus", "composer")

    if isinstance(event, TranscriptLoaded):
        view = state.thread_view(event.thread_id)
        if view is None or event.request_version < view.projection_version:
            return Reduction(state, _EMPTY_HINTS)
        expected = None if view.detail is None else view.detail.continuation_id
        if event.page.continuation_id != expected and not (
            expected is None
            and event.page.continuation_id is not None
            and event.page.continuation_id.startswith("initial:")
        ):
            return Reduction(state, _EMPTY_HINTS)
        incoming = _retained_blocks(event.thread_id, event.page)
        if event.prepend:
            timeline = _merge_prepend(view.timeline, incoming)
            preserve = view.reading_anchor
        else:
            provisional = tuple(block for block in view.timeline if block.provisional)
            timeline = _deduplicate((*incoming, *provisional))
            preserve = None
        view = replace(
            view,
            timeline=_evict_timeline(timeline, view),
            transcript_continuation_id=event.page.continuation_id,
            older_cursor=event.page.next_cursor,
            projection_version=event.request_version,
        )
        next_state = _with_view(state, view)
        return Reduction(
            next_state,
            ProjectionHints(changed=frozenset({"focus"}), preserve_anchor=preserve),
        )

    if isinstance(event, LiveReceived):
        return _reduce_live(state, event)

    if isinstance(event, LiveUnavailable):
        view = state.thread_view(event.thread_id)
        next_state = state
        if view is not None:
            next_state = _with_view(state, replace(view, control_mode=ControlMode.UNAVAILABLE))
        next_state = _notice(
            next_state,
            severity="warning",
            message=event.failure.message,
            code=event.failure.code,
            thread_id=event.thread_id,
        )
        return _result(next_state, "focus", "composer", "notice")

    if isinstance(event, RootReceiptAccepted):
        thread_id = event.receipt.thread_id
        view = state.thread_view(thread_id) or ThreadViewState(thread_id=thread_id)
        block = TimelineBlock(
            block_id=f"receipt:{event.receipt.receipt_id}:user",
            thread_id=thread_id,
            receipt_id=event.receipt.receipt_id,
            kind=BlockKind.USER,
            status=BlockStatus.PROVISIONAL,
            source_text=event.submitted_text[:MAX_BLOCK_TEXT],
            provisional=True,
        )
        timeline = view.timeline if event.steering else _deduplicate((*view.timeline, block))
        current_operation = view.root_operation
        if current_operation is None or current_operation.receipt.receipt_id != event.receipt.receipt_id:
            operation = RootOperationView(
                receipt=event.receipt,
                status=RootOperationStatus.preparing,
                available_actions=("wait", "cancel"),
            )
        else:
            operation = current_operation
        view = replace(
            view,
            root_operation=operation,
            timeline=_evict_timeline(timeline, view),
            control_mode=_control_mode(view.detail, operation, view.decisions is not None),
            cancelling_receipt_id=None,
        )
        next_state = _with_view(state, view)
        if not event.steering:
            next_state = _set_draft(next_state, event.draft_key, text="", cursor=0)
        return _result(next_state, "focus", "composer", scroll_to_latest=view.follow_latest)

    if isinstance(event, RootOperationUpdated):
        return _apply_root_operation(state, event.operation)

    if isinstance(event, RootControlCompleted):
        operation = next(
            (
                view
                for view in state.thread_views
                if view.root_operation is not None and view.root_operation.receipt.receipt_id == event.result.receipt_id
            ),
            None,
        )
        if operation is None:
            return Reduction(state, _EMPTY_HINTS)
        view = operation
        next_state = state
        if event.action == "cancel" and event.result.accepted:
            view = replace(
                view,
                control_mode=ControlMode.CANCELLING,
                cancelling_receipt_id=event.result.receipt_id,
            )
        elif event.action == "steer" and event.result.accepted and event.draft_key is not None:
            next_state = _set_draft(next_state, event.draft_key, text="", cursor=0)
        next_state = _with_view(next_state, view)
        return _result(next_state, "focus", "composer")

    if isinstance(event, DraftDefaultsChanged):
        return _result(replace(state, draft_defaults=event.defaults), "composer")

    if isinstance(event, DraftChanged):
        references = tuple(item for item in event.skill_references if isinstance(item, SkillReference))
        next_state = _touch_draft(
            state,
            DraftState(
                key=event.key,
                text=event.text[:MAX_BLOCK_TEXT],
                cursor=max(0, min(event.cursor, len(event.text[:MAX_BLOCK_TEXT]))),
                project_paths=event.project_paths[:100],
                skill_references=references[:64],
            ),
        )
        return _result(next_state, "composer")

    if isinstance(event, DraftRestored):
        next_state = _set_draft(state, event.key, text=event.text, cursor=len(event.text))
        next_state = _notice(next_state, severity="warning", message=event.message, code=event.code)
        return _result(next_state, "composer", "notice")

    if isinstance(event, RouteChanged):
        mode = TerminalMode(event.mode)
        focused = state.focused_thread_id
        previous = state.previous_focused_thread_id
        if event.new_draft:
            focused = None
        elif mode is TerminalMode.FOCUS and event.thread_id is not None:
            focused = event.thread_id
            previous = event.thread_id
        return _result(
            replace(
                state,
                mode=mode,
                focused_thread_id=focused,
                previous_focused_thread_id=previous,
            ),
            "route",
            "focus" if mode is TerminalMode.FOCUS else "workbench",
        )

    if isinstance(event, WorkbenchSelectionChanged):
        return _result(
            replace(
                state,
                workbench=replace(state.workbench, selected_thread_id=event.thread_id),
            ),
            "workbench",
        )

    if isinstance(event, OverlayOpened):
        overlays = (*state.overlays, event.overlay)[-8:]
        return _result(replace(state, overlays=overlays), "overlay")

    if isinstance(event, OverlayClosed):
        return _result(replace(state, overlays=state.overlays[:-1]), "overlay")

    if isinstance(event, FollowLatestChanged):
        view = state.thread_view(event.thread_id)
        if view is None:
            return Reduction(state, _EMPTY_HINTS)
        view = replace(
            view,
            follow_latest=event.enabled,
            pending_output=0 if event.enabled else view.pending_output,
        )
        return _result(
            _with_view(state, view),
            "focus",
            scroll_to_latest=event.enabled,
        )

    if isinstance(event, ReadingAnchorChanged):
        view = state.thread_view(event.thread_id)
        if view is None:
            return Reduction(state, _EMPTY_HINTS)
        return _result(_with_view(state, replace(view, reading_anchor=event.anchor)), "focus")

    if isinstance(event, TimelineSelectionChanged):
        view = state.thread_view(event.thread_id)
        if view is None:
            return Reduction(state, _EMPTY_HINTS)
        selected = event.block_id if any(block.block_id == event.block_id for block in view.timeline) else None
        return _result(_with_view(state, replace(view, selected_block_id=selected)), "focus")

    if isinstance(event, CompletionAcknowledged):
        acknowledged = frozenset((*state.workbench.acknowledged_receipts, event.receipt_id))
        page = state.workbench.page
        workbench = replace(state.workbench, acknowledged_receipts=acknowledged)
        if page is not None:
            workbench = replace(workbench, page=_ranked_page(page, acknowledged))
        return _result(replace(state, workbench=workbench), "workbench")

    if isinstance(event, OperationFailed):
        next_state = state
        if event.draft_key is not None and event.draft_text is not None:
            next_state = _set_draft(
                next_state,
                event.draft_key,
                text=event.draft_text,
                cursor=len(event.draft_text),
            )
        next_state = _notice(
            next_state,
            severity="error",
            message=event.failure.message,
            code=event.failure.code,
            thread_id=event.thread_id,
        )
        return _result(next_state, "composer", "notice")

    raise TypeError(f"Unsupported terminal event: {type(event).__name__}")


def derive_control_mode(view: ThreadViewState | None, *, draft: bool = False) -> ControlMode:
    if draft:
        return ControlMode.DRAFT
    if view is None:
        return ControlMode.UNAVAILABLE
    if view.cancelling_receipt_id is not None:
        return ControlMode.CANCELLING
    return _control_mode(view.detail, view.root_operation, view.decisions is not None)


def ranked_workbench_rows(state: WorkbenchState) -> tuple[WorkbenchThreadView, ...]:
    if state.page is None:
        return ()
    return _rank_rows(state.page.rows, state.acknowledged_receipts)


def _control_mode(
    detail: ThreadDetail | None,
    operation: RootOperationView | None,
    has_decisions: bool,
) -> ControlMode:
    if operation is not None:
        if operation.status is RootOperationStatus.preparing:
            return ControlMode.PREPARING
        if operation.status is RootOperationStatus.running:
            return ControlMode.RUNNING
    if detail is None:
        return ControlMode.UNAVAILABLE
    if has_decisions or detail.deferred_requests:
        return ControlMode.AWAITING_DECISION
    return ControlMode.IDLE


def _reduce_live(state: TerminalState, event: LiveReceived) -> Reduction:
    source = event.event
    view = state.thread_view(source.thread_id)
    if view is None:
        return Reduction(state, _EMPTY_HINTS)
    if view.epoch != source.epoch:
        unavailable = replace(view, control_mode=ControlMode.UNAVAILABLE)
        next_state = _with_view(state, unavailable)
        next_state = _notice(
            next_state,
            severity="warning",
            message="Live presentation epoch changed; refreshing the focused Thread.",
            code="live_epoch_changed",
            thread_id=source.thread_id,
        )
        return _result(next_state, "focus", "composer", "notice")
    if source.sequence <= view.last_sequence:
        return Reduction(state, _EMPTY_HINTS)

    timeline = view.timeline
    added = 0
    if isinstance(source, StreamPartEvent):
        block_id = _live_part_id(source)
        kind = BlockKind.ASSISTANT if source.kind == "assistant" else BlockKind.REASONING
        existing = next((item for item in timeline if item.block_id == block_id), None)
        if existing is None:
            status = BlockStatus.CLOSED if source.action == "close" else BlockStatus.RUNNING
            block = TimelineBlock(
                block_id=block_id,
                thread_id=source.thread_id,
                run_id=source.run_id,
                execution_id=source.execution_id,
                kind=kind,
                status=status,
                source_text=source.text[:MAX_BLOCK_TEXT] or None,
                version=source.sequence,
                provisional=True,
            )
            timeline = (*timeline, block)
            added = max(1, len(source.text))
        elif source.sequence > existing.version:
            text = existing.source_text or ""
            if source.action in {"open", "append"}:
                text = (text + source.text)[:MAX_BLOCK_TEXT]
            status = BlockStatus.CLOSED if source.action == "close" else BlockStatus.RUNNING
            replacement = replace(
                existing,
                source_text=text or None,
                status=status,
                version=source.sequence,
            )
            timeline = tuple(replacement if item.block_id == block_id else item for item in timeline)
            added = len(source.text)
    elif isinstance(source, ToolEvent):
        block_id = _live_tool_id(source)
        existing = next((item for item in timeline if item.block_id == block_id), None)
        status = BlockStatus.CLOSED if source.action in {"close", "result"} else BlockStatus.RUNNING
        if existing is None:
            block = TimelineBlock(
                block_id=block_id,
                thread_id=source.thread_id,
                run_id=source.run_id,
                execution_id=source.execution_id,
                kind=BlockKind.TOOL,
                status=status,
                source_text=source.text[:MAX_BLOCK_TEXT] or None,
                summary=source.tool_name,
                detail_available=source.action in {"arguments", "result"},
                version=source.sequence,
                provisional=True,
            )
            timeline = (*timeline, block)
            added = 1
        elif source.sequence > existing.version:
            text = ((existing.source_text or "") + source.text)[:MAX_BLOCK_TEXT]
            replacement = replace(
                existing,
                status=status,
                source_text=text or None,
                summary=source.tool_name or existing.summary,
                detail_available=existing.detail_available or source.action in {"arguments", "result"},
                version=source.sequence,
            )
            timeline = tuple(replacement if item.block_id == block_id else item for item in timeline)
            added = len(source.text)
    elif isinstance(source, RunHintEvent):
        if source.action == "error":
            block = TimelineBlock(
                block_id=f"live:{source.thread_id}:{source.run_id}:run-error",
                thread_id=source.thread_id,
                run_id=source.run_id,
                execution_id=source.execution_id,
                kind=BlockKind.FAILURE,
                status=BlockStatus.FAILED,
                summary=source.message or "Run error",
                version=source.sequence,
                provisional=True,
            )
            timeline = _deduplicate((*timeline, block))
            added = 1
    elif isinstance(source, UnknownLiveEvent):
        kind = BlockKind.CHILD if source.execution_id is not None else BlockKind.NOTICE
        block = TimelineBlock(
            block_id=f"live:{source.thread_id}:{source.run_id}:event:{source.sequence}",
            thread_id=source.thread_id,
            run_id=source.run_id,
            execution_id=source.execution_id,
            kind=kind,
            status=BlockStatus.CLOSED,
            summary=source.summary,
            version=source.sequence,
            provisional=True,
        )
        timeline = (*timeline, block)
        added = 1

    pending = view.pending_output
    if not view.follow_latest:
        pending += added
    view = replace(
        view,
        timeline=_evict_timeline(timeline, view),
        last_sequence=source.sequence,
        pending_output=pending,
    )
    return _result(
        _with_view(state, view),
        "focus",
        scroll_to_latest=view.follow_latest and added > 0,
    )


def _apply_root_operation(state: TerminalState, operation: RootOperationView) -> Reduction:
    thread_id = operation.receipt.thread_id
    view = state.thread_view(thread_id)
    if view is None:
        return Reduction(state, _EMPTY_HINTS)
    current = view.root_operation
    if (
        current is not None
        and current.status not in _TERMINAL_OPERATION_STATUSES
        and current.receipt.receipt_id != operation.receipt.receipt_id
    ):
        return Reduction(state, _EMPTY_HINTS)
    cancelling = view.cancelling_receipt_id
    if operation.status in _TERMINAL_OPERATION_STATUSES:
        cancelling = None
    mode = _control_mode(view.detail, operation, view.decisions is not None)
    if operation.status is RootOperationStatus.suspended and view.decisions is None:
        mode = ControlMode.UNAVAILABLE
    if cancelling is not None:
        mode = ControlMode.CANCELLING
    timeline = view.timeline
    if operation.status in _TERMINAL_OPERATION_STATUSES:
        status = {
            RootOperationStatus.completed: BlockStatus.CLOSED,
            RootOperationStatus.suspended: BlockStatus.CLOSED,
            RootOperationStatus.failed: BlockStatus.FAILED,
            RootOperationStatus.cancelled: BlockStatus.CANCELLED,
        }[operation.status]
        timeline = tuple(
            replace(block, status=status, provisional=True)
            if block.receipt_id == operation.receipt.receipt_id and block.provisional
            else block
            for block in timeline
        )
        if operation.failure is not None:
            failure = TimelineBlock(
                block_id=f"receipt:{operation.receipt.receipt_id}:failure",
                thread_id=thread_id,
                receipt_id=operation.receipt.receipt_id,
                run_id=operation.run_id,
                kind=BlockKind.FAILURE,
                status=BlockStatus.FAILED,
                summary=operation.failure.message,
                detail_available=operation.failure.details is not None,
                provisional=True,
            )
            timeline = _deduplicate((*timeline, failure))
    unretained = bool(operation.outcome is not None and operation.outcome.continuation.status != "selected")
    view = replace(
        view,
        root_operation=operation,
        control_mode=mode,
        cancelling_receipt_id=cancelling,
        timeline=_evict_timeline(timeline, view),
        unretained_output=unretained,
    )
    next_state = _with_view(state, view)
    if unretained:
        next_state = _notice(
            next_state,
            severity="warning",
            message="The latest output is visible but was not selected as retained continuation truth.",
            code="continuation_not_selected",
            thread_id=thread_id,
        )
    return _result(next_state, "focus", "composer", "notice")


def _retained_blocks(thread_id: str, page: TranscriptPage) -> tuple[TimelineBlock, ...]:
    continuation = page.continuation_id or "initial"
    result: list[TimelineBlock] = []
    tool_indexes: dict[str, int] = {}
    for entry in page.entries:
        for part_index, part in enumerate(entry.parts):
            if part.kind == "user":
                kind = BlockKind.USER
            elif part.kind == "assistant":
                kind = BlockKind.ASSISTANT
            elif part.kind == "thinking":
                kind = BlockKind.REASONING
            elif part.kind in {"tool_call", "tool_result", "retry"}:
                kind = BlockKind.TOOL
            else:
                kind = BlockKind.NOTICE
            block_id = (
                f"retained:{thread_id}:{continuation}:tool:{part.tool_call_id}"
                if part.tool_call_id is not None
                else f"retained:{thread_id}:{continuation}:{entry.position}:{part_index}"
            )
            block = TimelineBlock(
                block_id=block_id,
                thread_id=thread_id,
                kind=kind,
                status=BlockStatus.CLOSED,
                source_text=part.text,
                summary=part.tool_name,
                detail_available=part.value is not None or part.value_omitted,
                retained_position=entry.position,
            )
            if part.tool_call_id is not None and block_id in tool_indexes:
                index = tool_indexes[block_id]
                previous = result[index]
                result[index] = replace(
                    previous,
                    source_text=block.source_text or previous.source_text,
                    summary=block.summary or previous.summary,
                    detail_available=previous.detail_available or block.detail_available,
                    retained_position=min(
                        entry.position if previous.retained_position is None else previous.retained_position,
                        entry.position,
                    ),
                )
            else:
                if part.tool_call_id is not None:
                    tool_indexes[block_id] = len(result)
                result.append(block)
    return tuple(result)


def _merge_prepend(
    current: tuple[TimelineBlock, ...],
    incoming: tuple[TimelineBlock, ...],
) -> tuple[TimelineBlock, ...]:
    existing = {block.block_id for block in current}
    return (*tuple(block for block in incoming if block.block_id not in existing), *current)


def _deduplicate(blocks: tuple[TimelineBlock, ...]) -> tuple[TimelineBlock, ...]:
    result: list[TimelineBlock] = []
    indexes: dict[str, int] = {}
    for block in blocks:
        index = indexes.get(block.block_id)
        if index is None:
            indexes[block.block_id] = len(result)
            result.append(block)
        elif block.version >= result[index].version:
            result[index] = block
    return tuple(result)


def _evict_timeline(
    timeline: tuple[TimelineBlock, ...],
    view: ThreadViewState,
) -> tuple[TimelineBlock, ...]:
    if len(timeline) <= MAX_TIMELINE_BLOCKS:
        return timeline
    protected = {block.block_id for block in timeline if block.status in {BlockStatus.PROVISIONAL, BlockStatus.RUNNING}}
    if view.selected_block_id is not None:
        protected.add(view.selected_block_id)
    if view.reading_anchor is not None:
        protected.add(view.reading_anchor.block_id)
    removable = len(timeline) - MAX_TIMELINE_BLOCKS
    kept: list[TimelineBlock] = []
    for block in timeline:
        if removable > 0 and block.block_id not in protected:
            removable -= 1
            continue
        kept.append(block)
    return tuple(kept)


def _live_part_id(event: StreamPartEvent) -> str:
    execution = event.execution_id or "root"
    return f"live:{event.thread_id}:{event.run_id}:{execution}:part:{event.part_id}"


def _live_tool_id(event: ToolEvent) -> str:
    execution = event.execution_id or "root"
    return f"live:{event.thread_id}:{event.run_id}:{execution}:tool:{event.tool_call_id}"


def _touch_draft(state: TerminalState, draft: DraftState) -> TerminalState:
    clock = state.logical_clock + 1
    previous = state.draft(draft.key)
    revision = previous.editor_revision if previous is not None else draft.editor_revision
    touched = replace(draft, editor_revision=revision, touched=clock)
    drafts = [item for item in state.drafts if item.key != draft.key]
    drafts.append(touched)
    drafts.sort(key=lambda item: item.touched, reverse=True)
    return replace(state, drafts=tuple(drafts[:MAX_DRAFTS]), logical_clock=clock)


def _set_draft(state: TerminalState, key: str, *, text: str, cursor: int) -> TerminalState:
    previous = state.draft(key) or DraftState(key=key)
    return _touch_draft(
        state,
        replace(
            previous,
            text=text[:MAX_BLOCK_TEXT],
            cursor=max(0, min(cursor, len(text[:MAX_BLOCK_TEXT]))),
        ),
    )


def _with_view(state: TerminalState, replacement: ThreadViewState) -> TerminalState:
    views = [item for item in state.thread_views if item.thread_id != replacement.thread_id]
    views.append(replacement)
    if len(views) > 8:
        focused = replacement.thread_id
        views = [item for item in views if item.thread_id == focused] + [
            item for item in views if item.thread_id != focused
        ][-7:]
    return replace(state, thread_views=tuple(views))


def _notice(
    state: TerminalState,
    *,
    severity: Literal["info", "warning", "error"],
    message: str,
    code: str | None = None,
    thread_id: str | None = None,
) -> TerminalState:
    clock = state.logical_clock + 1
    notice = TerminalNotice(
        notice_id=f"notice-{clock}",
        severity=severity,
        message=message[: 32 * 1024],
        code=code,
        thread_id=thread_id,
    )
    return replace(
        state,
        notices=(*state.notices, notice)[-MAX_NOTICES:],
        logical_clock=clock,
    )


def _ranked_page(page: WorkbenchPage, acknowledged: frozenset[str]) -> WorkbenchPage:
    return page.model_copy(update={"rows": _rank_rows(page.rows, acknowledged)})


def _rank_rows(
    rows: tuple[WorkbenchThreadView, ...],
    acknowledged: frozenset[str],
) -> tuple[WorkbenchThreadView, ...]:
    return tuple(sorted(rows, key=lambda row: _attention_key(row, acknowledged)))


def _attention_key(row: WorkbenchThreadView, acknowledged: frozenset[str]) -> tuple[int, float, str]:
    if row.pending_decision is not None:
        rank = 0
    elif (
        row.latest_operation is not None
        and row.latest_operation.receipt.receipt_id not in acknowledged
        and row.latest_operation.status in _TERMINAL_OPERATION_STATUSES
    ):
        rank = 1
    elif row.thread.root_activity.state.value != "inactive" or row.children.active:
        rank = 2
    else:
        rank = 3
    return rank, -_timestamp(row.thread.updated_at), row.thread.thread_id


def _timestamp(value: datetime) -> float:
    return value.timestamp()


def _result(
    state: TerminalState,
    *changed: Literal["lifecycle", "route", "workbench", "focus", "composer", "overlay", "notice"],
    scroll_to_latest: bool = False,
) -> Reduction:
    return Reduction(
        state,
        ProjectionHints(changed=frozenset(changed), scroll_to_latest=scroll_to_latest),
    )


__all__ = ["derive_control_mode", "ranked_workbench_rows", "reduce_terminal"]
