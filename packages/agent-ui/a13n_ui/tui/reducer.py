"""Pure deterministic reducer for terminal semantic state."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from a13n_ui.surfaces import (
    DecisionBatchView,
    LaunchProjectSelected,
    RootOperationStatus,
    RootOperationView,
    SkillReference,
    TaskPage,
    ThreadActivityPage,
    ThreadDetail,
    ThreadFocusSnapshot,
    TranscriptPage,
)
from a13n_ui.tui.events import (
    ChildControlCompleted,
    ClosingStarted,
    CompletionApplied,
    CompletionClosed,
    CompletionLoaded,
    ConfigurationConflictCleared,
    ConfigurationConflictRecorded,
    DecisionDraftUpdated,
    DecisionPositionChanged,
    DecisionSubmitted,
    DecisionValidationFailed,
    DisclosureChanged,
    DraftChanged,
    DraftDefaultsChanged,
    DraftRekeyed,
    DraftRestored,
    DraftSubmitted,
    EditorDraftApplied,
    FocusLoaded,
    FollowLatestChanged,
    LiveReceived,
    LiveUnavailable,
    OperationFailed,
    OverlayClosed,
    OverlayOpened,
    ProjectsLoaded,
    ReadingAnchorChanged,
    ReviewLoaded,
    RootControlCompleted,
    RootOperationUpdated,
    RootReceiptAccepted,
    RouteChanged,
    RunHintEvent,
    SelectorsLoaded,
    SkillCatalogLoaded,
    StartupFailed,
    StartupReady,
    StartupStarted,
    StreamPartEvent,
    TaskChangedEvent,
    TerminalEvent,
    ThreadActivityLoaded,
    ThreadPickerLoaded,
    TimelineSelectionChanged,
    ToolEvent,
    TranscriptLoaded,
    UnknownLiveEvent,
)
from a13n_ui.tui.models import (
    MAX_BLOCK_TEXT,
    MAX_DRAFTS,
    MAX_NOTICES,
    MAX_TIMELINE_BLOCKS,
    BlockKind,
    BlockStatus,
    ControlMode,
    DecisionAnswerDraft,
    DecisionSessionState,
    DraftState,
    OverlayState,
    ProjectionHints,
    Reduction,
    ReviewState,
    TerminalLifecycle,
    TerminalNotice,
    TerminalState,
    ThreadActivityState,
    ThreadViewState,
    TimelineBlock,
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
        focused = event.explicit_thread_id
        defaults = state.draft_defaults.model_copy(
            update={
                "project_id": state.draft_defaults.project_id or launch_project_id,
            }
        )
        next_state = replace(
            state,
            lifecycle=TerminalLifecycle.READY,
            launch_resolution=event.launch,
            launch_project_id=launch_project_id,
            project_filter_id=event.thread_activity.project_id,
            focused_thread_id=focused,
            draft_defaults=defaults,
            thread_activity=ThreadActivityState(
                page=event.thread_activity,
                projection_version=1,
            ),
        )
        return _result(next_state, "lifecycle", "route", "thread_activity", "focus")

    if isinstance(event, StartupFailed):
        next_state = _notice(
            replace(
                state,
                lifecycle=TerminalLifecycle.FAILED,
                overlays=(),
                review=None,
                completion=None,
                configuration_conflict=None,
            ),
            severity="error",
            message=event.failure.message,
            code=event.failure.code,
        )
        return _result(next_state, "lifecycle", "overlay", "notice")

    if isinstance(event, ClosingStarted):
        return _result(
            replace(
                state,
                lifecycle=TerminalLifecycle.CLOSING,
                overlays=(),
                review=None,
                completion=None,
                configuration_conflict=None,
            ),
            "lifecycle",
            "overlay",
        )

    if isinstance(event, ThreadActivityLoaded):
        if event.request_version < state.thread_activity.projection_version:
            return Reduction(state, _EMPTY_HINTS)
        page = event.page
        if (
            event.append
            and state.thread_activity.page is not None
            and state.thread_activity.page.project_id == event.page.project_id
            and state.thread_activity.query == event.query
        ):
            rows = tuple(
                {item.thread.thread_id: item for item in (*state.thread_activity.page.rows, *event.page.rows)}.values()
            )
            page = ThreadActivityPage(
                project_id=event.page.project_id,
                rows=rows,
                total=event.page.total,
                next_cursor=event.page.next_cursor,
            )
        thread_activity = replace(
            state.thread_activity,
            page=page,
            query=event.query,
            projection_version=event.request_version,
        )
        return _result(
            replace(state, thread_activity=thread_activity, project_filter_id=event.page.project_id), "thread_activity"
        )

    if isinstance(event, ProjectsLoaded):
        if event.request_version < state.overlay_request_version:
            return Reduction(state, _EMPTY_HINTS)
        return _result(
            replace(
                state,
                projects=event.projects[:256],
                overlay_request_version=event.request_version,
            ),
            "overlay",
        )

    if isinstance(event, SelectorsLoaded):
        if event.request_version < state.overlay_request_version:
            return Reduction(state, _EMPTY_HINTS)
        return _result(
            replace(
                state,
                selectors=event.selectors,
                overlay_request_version=event.request_version,
            ),
            "overlay",
        )

    if isinstance(event, SkillCatalogLoaded):
        if event.request_version < state.overlay_request_version:
            return Reduction(state, _EMPTY_HINTS)
        return _result(
            replace(
                state,
                skill_catalog=event.catalog,
                overlay_request_version=event.request_version,
            ),
            "overlay",
        )

    if isinstance(event, ThreadPickerLoaded):
        if event.request_version < state.overlay_request_version:
            return Reduction(state, _EMPTY_HINTS)
        page = event.page
        if event.append and state.thread_picker is not None:
            if state.thread_picker.project_id != page.project_id or state.thread_picker_query != event.query:
                return Reduction(state, _EMPTY_HINTS)
            rows = tuple({row.thread.thread_id: row for row in (*state.thread_picker.rows, *page.rows)}.values())
            page = page.model_copy(update={"rows": rows})
        return _result(
            replace(
                state,
                thread_picker=page,
                thread_picker_query=event.query,
                overlay_request_version=event.request_version,
            ),
            "overlay",
        )

    if isinstance(event, CompletionLoaded):
        current = state.completion
        if current is not None and event.completion.request_version < current.request_version:
            return Reduction(state, _EMPTY_HINTS)
        draft = state.draft(event.completion.key)
        prefix = "@" if event.completion.kind == "path" else "$"
        if (
            draft is None
            or draft.text[event.completion.token_start : event.completion.token_end] != prefix + event.completion.query
        ):
            return Reduction(state, _EMPTY_HINTS)
        return _result(replace(state, completion=event.completion), "composer", "overlay")

    if isinstance(event, CompletionClosed):
        if state.completion is None:
            return Reduction(state, _EMPTY_HINTS)
        return _result(replace(state, completion=None), "composer", "overlay")

    if isinstance(event, CompletionApplied):
        completion = state.completion
        draft = state.draft(event.key)
        if (
            completion is None
            or draft is None
            or completion.key != event.key
            or completion.token_start != event.token_start
            or completion.token_end != event.token_end
        ):
            return Reduction(state, _EMPTY_HINTS)
        start = max(0, min(event.token_start, len(draft.text)))
        end = max(start, min(event.token_end, len(draft.text)))
        text = (draft.text[:start] + event.replacement + draft.text[end:])[:MAX_BLOCK_TEXT]
        skill_references = draft.skill_references
        if event.skill_reference is not None:
            skill_references = (
                *(item for item in skill_references if item.name != event.skill_reference.name),
                event.skill_reference,
            )
        project_paths = draft.project_paths
        if event.project_path is not None and event.project_path not in project_paths:
            project_paths = (*project_paths, event.project_path)[-100:]
        next_state = _touch_draft(
            replace(state, completion=None),
            replace(
                draft,
                text=text,
                cursor=min(len(text), start + len(event.replacement)),
                skill_references=skill_references[-64:],
                editor_revision=draft.editor_revision + 1,
                project_paths=project_paths,
            ),
        )
        return _result(next_state, "composer", "overlay")

    if isinstance(event, FocusLoaded):
        thread_id = event.snapshot.thread.thread.thread_id
        current = state.thread_view(thread_id)
        if current is not None and event.request_version < current.projection_version:
            return Reduction(state, _EMPTY_HINTS)
        timeline = () if current is None else current.timeline
        if current is not None and current.epoch not in {None, event.snapshot.epoch}:
            timeline = tuple(block for block in timeline if not block.provisional)
        tasks = event.snapshot.tasks
        operation = event.snapshot.root_operation
        tasks_run_id = None if operation is None else operation.run_id
        if (
            current is not None
            and current.epoch == event.snapshot.epoch
            and tasks_run_id is not None
            and current.tasks_run_id == tasks_run_id
            and current.detail is not None
            and current.detail.continuation_id == event.snapshot.thread.continuation_id
            and current.tasks.version is not None
            and (tasks.version is None or current.tasks.version > tasks.version)
        ):
            tasks = current.tasks
        timeline = tuple(
            block
            for block in timeline
            if block.kind not in {BlockKind.TASK, BlockKind.CHILD} or block.execution_id is not None
        )
        timeline = _deduplicate(
            (*timeline, *_snapshot_activity_blocks(event.snapshot.model_copy(update={"tasks": tasks})))
        )
        timeline = _settle_child_blocks(timeline, event.snapshot)
        decision_session, stale_decision_session = _decision_sessions(
            thread_id,
            current,
            event.decisions,
            event.snapshot.root_operation,
        )
        view = ThreadViewState(
            thread_id=thread_id,
            detail=event.snapshot.thread,
            snapshot=event.snapshot,
            root_operation=event.snapshot.root_operation,
            settled_operations=() if current is None else current.settled_operations,
            tasks=tasks,
            tasks_run_id=tasks_run_id,
            decisions=event.decisions,
            decision_session=decision_session,
            stale_decision_session=stale_decision_session,
            timeline=_evict_timeline(timeline, current or ThreadViewState(thread_id=thread_id)),
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
            newer_history_omitted=False if current is None else current.newer_history_omitted,
            follow_latest=True if current is None else current.follow_latest,
            pending_output=0 if current is None else current.pending_output,
            selected_block_id=None if current is None else current.selected_block_id,
            reading_anchor=None if current is None else current.reading_anchor,
            cancelling_receipt_id=None if current is None else current.cancelling_receipt_id,
            unretained_output=False if current is None else current.unretained_output,
        )
        next_state = _with_view(state, view)
        if event.snapshot.root_operation is not None:
            return _apply_root_operation(next_state, event.snapshot.root_operation)
        return _result(next_state, "focus", "composer")

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
            current_activity = tuple(
                block
                for block in view.timeline
                if (block.provisional or block.kind in {BlockKind.TASK, BlockKind.CHILD})
                and not (
                    block.retained_continuation_id is not None
                    and block.retained_continuation_id == event.page.continuation_id
                )
            )
            timeline = _deduplicate((*incoming, *current_activity))
            preserve = None
        bounded = _evict_timeline(timeline, view, from_end=event.prepend)
        view = replace(
            view,
            timeline=bounded,
            newer_history_omitted=(event.prepend and (view.newer_history_omitted or len(bounded) < len(timeline))),
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
        timeline = view.timeline if event.steering or not event.echo else _deduplicate((*view.timeline, block))
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
        if event.clear_draft:
            next_state = _clear_draft(next_state, event.draft_key)
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
            next_state = _clear_draft(next_state, event.draft_key)
        next_state = _with_view(next_state, view)
        return _result(next_state, "focus", "composer")

    if isinstance(event, ChildControlCompleted):
        status = "accepted" if event.result.accepted else "rejected"
        next_state = _notice(
            state,
            severity="info" if event.result.accepted else "warning",
            message=f"Child {event.action} {status}.",
            thread_id=event.parent_thread_id,
        )
        return _result(next_state, "focus", "notice")

    if isinstance(event, DecisionDraftUpdated):
        view = state.thread_view(event.thread_id)
        if view is None or view.decision_session is None:
            return Reduction(state, _EMPTY_HINTS)
        session = view.decision_session
        if event.draft.request_id not in session.request_ids:
            return Reduction(state, _EMPTY_HINTS)
        draft = replace(
            event.draft,
            question_answers=tuple(
                (question_text[:8192], tuple(value[:4096] for value in values[:4]))
                for question_text, values in event.draft.question_answers[:4]
            ),
            response_text=event.draft.response_text[:MAX_BLOCK_TEXT],
            payload_text=event.draft.payload_text[:MAX_BLOCK_TEXT],
            denial_reason=event.draft.denial_reason[:MAX_BLOCK_TEXT],
        )
        answers = tuple(item for item in session.answers if item.request_id != draft.request_id)
        answers = (*answers, draft)
        session = replace(session, answers=answers, validation_message=None)
        return _result(_with_view(state, replace(view, decision_session=session)), "focus", "composer")

    if isinstance(event, DecisionPositionChanged):
        view = state.thread_view(event.thread_id)
        if view is None or view.decision_session is None or view.decisions is None:
            return Reduction(state, _EMPTY_HINTS)
        request_index = max(0, min(event.request_index, len(view.decisions.requests) - 1))
        request = view.decisions.requests[request_index]
        question_count = len(request.questions) if request.kind == "question" else 1
        question_index = max(0, min(event.question_index, question_count - 1))
        session = replace(
            view.decision_session,
            request_index=request_index,
            question_index=question_index,
            validation_message=None,
        )
        return _result(_with_view(state, replace(view, decision_session=session)), "focus", "composer")

    if isinstance(event, DecisionValidationFailed):
        view = state.thread_view(event.thread_id)
        if view is None or view.decision_session is None:
            return Reduction(state, _EMPTY_HINTS)
        session = replace(view.decision_session, validation_message=event.message)
        return _result(_with_view(state, replace(view, decision_session=session)), "focus", "composer")

    if isinstance(event, DecisionSubmitted):
        view = state.thread_view(event.thread_id)
        if view is None or view.decision_session is None:
            return Reduction(state, _EMPTY_HINTS)
        session = replace(view.decision_session, submitted_receipt_id=event.receipt_id)
        return _result(_with_view(state, replace(view, decision_session=session)), "focus", "composer")

    if isinstance(event, ReviewLoaded):
        if state.review is not None and event.request_version < state.review.request_version:
            return Reduction(state, _EMPTY_HINTS)
        overlays = (*state.overlays, OverlayState(kind="review", key=event.key))[-8:]
        return _result(
            replace(
                state,
                review=ReviewState(
                    key=event.key,
                    view=event.view,
                    request_version=event.request_version,
                    thread_id=event.thread_id,
                    execution_id=event.execution_id,
                    available_actions=event.available_actions,
                ),
                overlays=overlays,
            ),
            "overlay",
        )

    if isinstance(event, DisclosureChanged):
        if event.kind == "reasoning":
            return _result(replace(state, show_reasoning=not state.show_reasoning), "focus")
        return _result(replace(state, show_tool_details=not state.show_tool_details), "focus")

    if isinstance(event, DraftDefaultsChanged):
        return _result(replace(state, draft_defaults=event.defaults), "composer")

    if isinstance(event, DraftChanged):
        references = tuple(item for item in event.skill_references if isinstance(item, SkillReference))
        previous = state.draft(event.key) or DraftState(key=event.key)
        text = event.text[:MAX_BLOCK_TEXT]
        paths = tuple(
            path for path in event.project_paths[:100] if _has_exact_marker(text, f"@{path}", allow_trailing_slash=True)
        )
        references = tuple(reference for reference in references[:64] if _has_exact_marker(text, f"${reference.name}"))
        content_changed = (
            text != previous.text or paths != previous.project_paths or references != previous.skill_references
        )
        next_state = _touch_draft(
            replace(
                state,
                completion=None
                if state.completion is not None and state.completion.key == event.key
                else state.completion,
            ),
            DraftState(
                key=event.key,
                text=text,
                cursor=max(0, min(event.cursor, len(text))),
                project_paths=paths,
                skill_references=references,
                editor_revision=previous.editor_revision + int(content_changed),
            ),
        )
        return _result(next_state, "composer")

    if isinstance(event, DraftRestored):
        draft = state.draft(event.key)
        next_state = state
        if draft is not None and draft.editor_revision == event.expected_revision:
            next_state = _set_draft(state, event.key, text=event.text, cursor=len(event.text))
        next_state = _notice(next_state, severity="warning", message=event.message, code=event.code)
        return _result(next_state, "composer", "notice")

    if isinstance(event, EditorDraftApplied):
        draft = state.draft(event.key)
        if draft is None or draft.editor_revision != event.expected_revision:
            next_state = _notice(
                state,
                severity="warning",
                message="The draft changed while the external editor was open; its result was not applied.",
                code="editor_draft_changed",
            )
            return _result(next_state, "composer", "notice")
        text = event.text[:MAX_BLOCK_TEXT]
        paths = tuple(
            path for path in draft.project_paths if _has_exact_marker(text, f"@{path}", allow_trailing_slash=True)
        )
        references = tuple(
            reference for reference in draft.skill_references if _has_exact_marker(text, f"${reference.name}")
        )
        changed = text != draft.text or paths != draft.project_paths or references != draft.skill_references
        next_state = _touch_draft(
            state,
            replace(
                draft,
                text=text,
                cursor=len(text),
                project_paths=paths,
                skill_references=references,
                editor_revision=draft.editor_revision + int(changed),
            ),
        )
        return _result(next_state, "composer")

    if isinstance(event, DraftRekeyed):
        draft = state.draft(event.old_key)
        if draft is None or event.old_key == event.new_key:
            return Reduction(state, _EMPTY_HINTS)
        drafts = tuple(item for item in state.drafts if item.key not in {event.old_key, event.new_key})
        next_state = replace(state, drafts=drafts)
        next_state = _touch_draft(next_state, replace(draft, key=event.new_key))
        return _result(next_state, "composer")

    if isinstance(event, DraftSubmitted):
        if state.draft(event.key) is None:
            return Reduction(state, _EMPTY_HINTS)
        next_state = _clear_draft(state, event.key)
        return _result(replace(next_state, completion=None), "composer", "overlay")

    if isinstance(event, RouteChanged):
        focused = None if event.new_draft or event.clear_focus else event.thread_id
        return _result(replace(state, focused_thread_id=focused), "route", "focus")

    if isinstance(event, OverlayOpened):
        overlays = (*state.overlays, event.overlay)[-8:]
        conflict = None if event.overlay.kind == "configuration" else state.configuration_conflict
        return _result(
            replace(state, overlays=overlays, configuration_conflict=conflict),
            "overlay",
        )

    if isinstance(event, OverlayClosed):
        closing_review = bool(state.overlays and state.overlays[-1].kind == "review")
        closing_configuration = bool(state.overlays and state.overlays[-1].kind == "configuration")
        return _result(
            replace(
                state,
                overlays=state.overlays[:-1],
                review=None if closing_review else state.review,
                configuration_conflict=(None if closing_configuration else state.configuration_conflict),
            ),
            "overlay",
        )

    if isinstance(event, ConfigurationConflictRecorded):
        next_state = _notice(
            replace(state, configuration_conflict=event.conflict),
            severity="warning",
            message=event.conflict.message,
            code="thread_configuration_conflict",
            thread_id=event.conflict.thread_id,
        )
        return _result(next_state, "overlay", "notice")

    if isinstance(event, ConfigurationConflictCleared):
        if state.configuration_conflict is None:
            return Reduction(state, _EMPTY_HINTS)
        return _result(replace(state, configuration_conflict=None), "overlay")

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
    view = state.thread_view(source.root_thread_id or source.thread_id)
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
            thread_id=view.thread_id,
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
                root_thread_id=source.root_thread_id,
                parent_thread_id=source.parent_thread_id,
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
                root_thread_id=source.root_thread_id,
                parent_thread_id=source.parent_thread_id,
                run_id=source.run_id,
                execution_id=source.execution_id,
                kind=BlockKind.TOOL,
                status=status,
                tool_call_id=source.tool_call_id,
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
                root_thread_id=source.root_thread_id,
                parent_thread_id=source.parent_thread_id,
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
    elif isinstance(source, TaskChangedEvent):
        task = source.task
        block_id = f"task:{source.thread_id}:{source.run_id}:{task.task_id}"
        existing = next((block for block in timeline if block.block_id == block_id), None)
        if existing is None or task.version > existing.version:
            block = TimelineBlock(
                block_id=block_id,
                thread_id=source.thread_id,
                root_thread_id=source.root_thread_id,
                parent_thread_id=source.parent_thread_id,
                run_id=source.run_id,
                execution_id=source.execution_id,
                kind=BlockKind.TASK,
                status={
                    "pending": BlockStatus.PROVISIONAL,
                    "in_progress": BlockStatus.RUNNING,
                    "completed": BlockStatus.CLOSED,
                }[task.status],
                version=task.version,
                task_id=task.task_id,
                summary=task.subject,
                source_text=task.active_form,
                detail_available=True,
                provisional=True,
            )
            timeline = _deduplicate((*timeline, block))
            added = 1
        active_run_id = None if view.root_operation is None else view.root_operation.run_id
        if source.thread_id == view.thread_id and active_run_id in {None, source.run_id}:
            if view.tasks_run_id != source.run_id:
                baseline = TaskPage() if view.snapshot is None else view.snapshot.tasks
                view = replace(view, tasks=baseline, tasks_run_id=source.run_id)
        if (
            source.thread_id == view.thread_id
            and view.tasks_run_id == source.run_id
            and (view.tasks.version is None or source.task_state_version >= view.tasks.version)
        ):
            tasks = list(view.tasks.tasks)
            index = next((index for index, item in enumerate(tasks) if item.task_id == task.task_id), None)
            total = view.tasks.total
            if index is not None:
                if task.version > tasks[index].version:
                    tasks[index] = task
            else:
                if total == len(tasks) or (
                    source.created and (view.tasks.version is None or source.task_state_version > view.tasks.version)
                ):
                    total += 1
                if len(tasks) < 256:
                    tasks.append(task)
            view = replace(
                view,
                tasks=view.tasks.model_copy(
                    update={
                        "version": source.task_state_version,
                        "tasks": tuple(tasks),
                        "total": max(total, len(tasks)),
                        "omitted": max(0, total - len(tasks)),
                    }
                ),
            )
    elif isinstance(source, UnknownLiveEvent):
        kind = BlockKind.CHILD if source.execution_id is not None else BlockKind.NOTICE
        block = TimelineBlock(
            block_id=f"live:{source.thread_id}:{source.run_id}:event:{source.sequence}",
            thread_id=source.thread_id,
            root_thread_id=source.root_thread_id,
            parent_thread_id=source.parent_thread_id,
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

    for operation in view.settled_operations:
        timeline = _settle_root_blocks(timeline, operation)
    if view.snapshot is not None:
        timeline = _settle_child_blocks(timeline, view.snapshot)
    timeline = tuple(
        block
        for block in timeline
        if block.retained_continuation_id is None or block.retained_continuation_id != view.transcript_continuation_id
    )
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
    if operation.status in _TERMINAL_OPERATION_STATUSES:
        settled = tuple(
            item for item in view.settled_operations if item.receipt.receipt_id != operation.receipt.receipt_id
        )
        view = replace(
            view,
            settled_operations=(*settled, operation)[-16:],
            timeline=_settle_root_blocks(view.timeline, operation),
        )
    current = view.root_operation
    if (
        current is not None
        and current.status not in _TERMINAL_OPERATION_STATUSES
        and current.receipt.receipt_id != operation.receipt.receipt_id
    ):
        return _result(_with_view(state, view), "focus")
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


def _settle_root_blocks(timeline: tuple[TimelineBlock, ...], operation: RootOperationView) -> tuple[TimelineBlock, ...]:
    status = {
        RootOperationStatus.completed: BlockStatus.CLOSED,
        RootOperationStatus.suspended: BlockStatus.CLOSED,
        RootOperationStatus.failed: BlockStatus.FAILED,
        RootOperationStatus.cancelled: BlockStatus.CANCELLED,
    }[operation.status]
    continuation_id = (
        operation.outcome.continuation.continuation_id
        if operation.outcome is not None and operation.outcome.continuation.status == "selected"
        else None
    )
    return tuple(
        replace(block, status=status, retained_continuation_id=continuation_id)
        if block.provisional
        and block.kind not in {BlockKind.FAILURE, BlockKind.TASK, BlockKind.CHILD}
        and (
            block.receipt_id == operation.receipt.receipt_id
            or (
                operation.run_id is not None
                and block.run_id == operation.run_id
                and block.thread_id == operation.receipt.thread_id
                and block.execution_id is None
            )
        )
        else block
        for block in timeline
    )


def _settle_child_blocks(
    timeline: tuple[TimelineBlock, ...], snapshot: ThreadFocusSnapshot
) -> tuple[TimelineBlock, ...]:
    statuses: dict[tuple[str | None, str, str | None], BlockStatus] = {
        (child.execution_id, child.child_thread_id, child.child_run_id): {
            "succeeded": BlockStatus.CLOSED,
            "failed": BlockStatus.FAILED,
            "cancelled": BlockStatus.CANCELLED,
            "lost": BlockStatus.FAILED,
        }[child.persisted_status]
        for child in snapshot.children.executions
        if child.persisted_status != "running"
    }
    return tuple(
        replace(block, status=statuses[(block.execution_id, block.thread_id, block.run_id)])
        if block.provisional
        and block.status in {BlockStatus.RUNNING, BlockStatus.PROVISIONAL}
        and (block.execution_id, block.thread_id, block.run_id) in statuses
        else block
        for block in timeline
    )


def _decision_sessions(
    thread_id: str,
    current: ThreadViewState | None,
    decisions: DecisionBatchView | None,
    operation: RootOperationView | None,
) -> tuple[DecisionSessionState | None, DecisionSessionState | None]:
    if decisions is None:
        return None, None if current is None else current.stale_decision_session
    request_ids = tuple(item.request_id for item in decisions.requests)
    previous = None if current is None else current.decision_session
    stale = None if current is None else current.stale_decision_session
    if (
        previous is not None
        and operation is not None
        and previous.submitted_receipt_id == operation.receipt.receipt_id
        and operation.outcome is not None
        and operation.outcome.continuation.status == "selected"
        and operation.outcome.continuation.continuation_id != previous.continuation_id
    ):
        previous = None
        stale = None
    if (
        previous is not None
        and previous.continuation_id == decisions.continuation_id
        and previous.request_ids == request_ids
    ):
        return previous, stale
    if previous is not None and _decision_has_input(previous):
        stale = previous
    return (
        DecisionSessionState(
            thread_id=thread_id,
            continuation_id=decisions.continuation_id,
            request_ids=request_ids,
            answers=tuple(DecisionAnswerDraft(request_id=request_id) for request_id in request_ids),
        ),
        stale,
    )


def _decision_has_input(session: DecisionSessionState) -> bool:
    return any(
        draft.question_answers
        or draft.response_text
        or draft.action is not None
        or draft.payload_text
        or draft.denial_reason
        for draft in session.answers
    )


def _snapshot_activity_blocks(snapshot: ThreadFocusSnapshot) -> tuple[TimelineBlock, ...]:
    thread_id = snapshot.thread.thread.thread_id
    blocks: list[TimelineBlock] = []
    for task in snapshot.tasks.tasks:
        status = {
            "pending": BlockStatus.PROVISIONAL,
            "in_progress": BlockStatus.RUNNING,
            "completed": BlockStatus.CLOSED,
        }[task.status]
        blocks.append(
            TimelineBlock(
                block_id=f"task:{thread_id}:{None if snapshot.root_operation is None else snapshot.root_operation.run_id}:{task.task_id}",
                thread_id=thread_id,
                task_id=task.task_id,
                kind=BlockKind.TASK,
                status=status,
                version=task.version,
                summary=task.subject,
                source_text=task.active_form,
                detail_available=True,
            )
        )
    if not snapshot.tasks.available or snapshot.tasks.omitted:
        detail = (
            "Task state is unavailable."
            if not snapshot.tasks.available
            else f"{snapshot.tasks.omitted} task(s) omitted."
        )
        blocks.append(
            TimelineBlock(
                block_id=f"task:{thread_id}:availability",
                thread_id=thread_id,
                kind=BlockKind.NOTICE,
                status=BlockStatus.CLOSED,
                summary=detail,
            )
        )
    for child in snapshot.children.executions:
        status = {
            "running": BlockStatus.RUNNING,
            "succeeded": BlockStatus.CLOSED,
            "failed": BlockStatus.FAILED,
            "cancelled": BlockStatus.CANCELLED,
            "lost": BlockStatus.FAILED,
        }[child.persisted_status]
        summary = f"{child.subagent_name} - {child.persisted_status}"
        if child.local_status == "unavailable" and child.persisted_status == "running":
            summary = f"{child.subagent_name} - unavailable"
        blocks.append(
            TimelineBlock(
                block_id=f"child:{thread_id}:{child.execution_id}",
                thread_id=thread_id,
                execution_id=child.execution_id,
                run_id=child.child_run_id,
                kind=BlockKind.CHILD,
                status=status,
                version=child.activity.sequence,
                summary=summary,
                source_text=child.activity.output_preview or None,
                detail_available=True,
                available_actions=child.available_actions,
            )
        )
    omitted_children = snapshot.children.total - len(snapshot.children.executions)
    if omitted_children > 0:
        blocks.append(
            TimelineBlock(
                block_id=f"child:{thread_id}:omitted",
                thread_id=thread_id,
                kind=BlockKind.NOTICE,
                status=BlockStatus.CLOSED,
                summary=f"{omitted_children} child execution(s) omitted.",
            )
        )
    return tuple(blocks)


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
                tool_call_id=part.tool_call_id,
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
    *,
    from_end: bool = False,
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
    for block in reversed(timeline) if from_end else timeline:
        if removable > 0 and block.block_id not in protected:
            removable -= 1
            continue
        kept.append(block)
    return tuple(reversed(kept)) if from_end else tuple(kept)


def _live_part_id(event: StreamPartEvent) -> str:
    execution = event.execution_id or "root"
    return f"live:{event.thread_id}:{event.run_id}:{execution}:part:{event.part_id}"


def _live_tool_id(event: ToolEvent) -> str:
    execution = event.execution_id or "root"
    return f"live:{event.thread_id}:{event.run_id}:{execution}:tool:{event.tool_call_id}"


def _touch_draft(state: TerminalState, draft: DraftState) -> TerminalState:
    clock = state.logical_clock + 1
    touched = replace(draft, touched=clock)
    drafts = [item for item in state.drafts if item.key != draft.key]
    drafts.append(touched)
    drafts.sort(key=lambda item: item.touched, reverse=True)
    return replace(state, drafts=tuple(drafts[:MAX_DRAFTS]), logical_clock=clock)


def _clear_draft(state: TerminalState, key: str) -> TerminalState:
    previous = state.draft(key) or DraftState(key=key)
    changed = bool(previous.text or previous.project_paths or previous.skill_references)
    return _touch_draft(
        state,
        replace(
            previous,
            text="",
            cursor=0,
            project_paths=(),
            skill_references=(),
            editor_revision=previous.editor_revision + int(changed),
        ),
    )


def _set_draft(state: TerminalState, key: str, *, text: str, cursor: int) -> TerminalState:
    previous = state.draft(key) or DraftState(key=key)
    bounded = text[:MAX_BLOCK_TEXT]
    return _touch_draft(
        state,
        replace(
            previous,
            text=bounded,
            cursor=max(0, min(cursor, len(bounded))),
            editor_revision=previous.editor_revision + int(bounded != previous.text),
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


def _has_exact_marker(text: str, marker: str, *, allow_trailing_slash: bool = False) -> bool:
    start = text.find(marker)
    while start >= 0:
        end = start + len(marker)
        if allow_trailing_slash and end < len(text) and text[end] == "/":
            end += 1
        before_boundary = start == 0 or text[start - 1].isspace()
        after_boundary = end == len(text) or text[end].isspace()
        if before_boundary and after_boundary:
            return True
        start = text.find(marker, start + 1)
    return False


def _result(
    state: TerminalState,
    *changed: Literal["lifecycle", "route", "thread_activity", "focus", "composer", "overlay", "notice"],
    scroll_to_latest: bool = False,
) -> Reduction:
    return Reduction(
        state,
        ProjectionHints(changed=frozenset(changed), scroll_to_latest=scroll_to_latest),
    )


__all__ = ["derive_control_mode", "reduce_terminal"]
