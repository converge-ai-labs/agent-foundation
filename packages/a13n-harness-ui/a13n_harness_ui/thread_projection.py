"""Detached Thread, Project, continuation, and transcript projections."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Literal

from a13n_harness.capabilities.working_state import WORKING_STATE_CAPABILITY_ID, Task, TaskState, WorkingState
from a13n_harness.model_context import user_prompt_content
from a13n_stream_protocol.messages import ContentMetadata, project_input_content
from anyio import Lock, to_thread
from pydantic import BaseModel, Field, JsonValue, TypeAdapter, ValidationError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.tools import DeferredToolRequests

from a13n_harness_ui.composition import CompositionAcceptanceService
from a13n_harness_ui.composition.models import ResolvedRunComposition
from a13n_harness_ui.conversation import excerpt_text, input_excerpt
from a13n_harness_ui.errors import ThreadError
from a13n_harness_ui.output_comment_models import RootOutputLocation, SavedOutputTarget
from a13n_harness_ui.storage import (
    LocalStore,
    ObjectRef,
    StoredContinuation,
    StoredThreadInitialState,
    Thread,
    ThreadConfiguration,
)
from a13n_harness_ui.storage.inspection import InspectionData, InspectionHeader, InspectionTurn
from a13n_harness_ui.surfaces import (
    AgentSourceView,
    ContextUsageView,
    DeferredRequestView,
    NotePage,
    NoteView,
    ProjectSummary,
    RootActivityState,
    RootActivityView,
    SurfaceModel,
    TaskPage,
    TaskView,
    ThreadConfigurationView,
    ThreadDetail,
    ThreadPage,
    ThreadSummary,
    TranscriptEntry,
    TranscriptInputPage,
    TranscriptPage,
    TranscriptPart,
    TranscriptTurn,
)
from a13n_harness_ui.tool_evidence import applied_edit

_MAX_CURSOR_BYTES = 3072
_MAX_TEXT = 64 * 1024
_MAX_JSON_BYTES = 64 * 1024
_JSON_ADAPTER = TypeAdapter(JsonValue)
_ROOT_INACTIVE = RootActivityView(state=RootActivityState.inactive)
type RootActivityLookup = Callable[[str], Awaitable[RootActivityView]]
type RootActivityBatchLookup = Callable[[tuple[str, ...]], Awaitable[Mapping[str, RootActivityView]]]


class _ThreadCursor(SurfaceModel):
    version: Literal["1"] = "1"
    kind: Literal["threads"] = "threads"
    query: str | None
    project_id: str | None = None
    project_ids: tuple[str, ...] | None = None
    project_ids_digest: str | None = None
    projectless: bool = False
    lead_thread_id: str | None = None
    independent_only: bool = False
    sort: Literal["updated", "activity", "touched"] = "updated"
    include_archived: bool
    archived_only: bool = False
    active_only: bool | None = None
    updated_at: datetime
    thread_id: str


class _TranscriptCursor(SurfaceModel):
    version: Literal["1"] = "1"
    kind: Literal["transcript"] = "transcript"
    thread_id: str
    continuation_id: str
    position: int
    direction: Literal["earlier", "later"] = "earlier"


class _InputCursor(SurfaceModel):
    thread_id: str
    continuation_id: str
    position: int = Field(ge=0)


class ThreadInspection(SurfaceModel):
    context_empty: bool = False
    run_composition: ObjectRef | None = None
    latest_request_tokens: int | None = None
    notes: NotePage = Field(default_factory=NotePage)
    tasks: TaskPage = Field(default_factory=TaskPage)


class ThreadProjectionService:
    """Read durable authority and return detached values for all App surfaces."""

    def __init__(
        self,
        *,
        store: LocalStore,
        configurations: CompositionAcceptanceService,
        root_activity: RootActivityLookup | None = None,
        root_activities: RootActivityBatchLookup | None = None,
    ) -> None:
        self._store = store
        self._configurations = configurations
        self._root_activity = root_activity
        self._root_activities = root_activities
        self._inspection_loads: dict[str, tuple[Lock, int]] = {}

    def set_root_activity_lookup(
        self,
        lookup: RootActivityLookup,
        batch_lookup: RootActivityBatchLookup | None = None,
    ) -> None:
        self._root_activity = lookup
        self._root_activities = batch_lookup

    async def get_thread(self, thread_id: str) -> ThreadSummary:
        thread = await self._required_thread(thread_id)
        return await self._summary(thread)

    async def list_threads(
        self,
        *,
        query: str | None = None,
        project_id: str | None = None,
        include_archived: bool = False,
        archived_only: bool = False,
        project_ids: tuple[str, ...] | None = None,
        projectless: bool = False,
        lead_thread_id: str | None = None,
        independent_only: bool = False,
        sort: Literal["updated", "activity", "touched"] = "updated",
        active_only: bool | None = None,
        active_thread_ids: tuple[str, ...] = (),
        cursor: str | None = None,
        limit: int = 20,
    ) -> ThreadPage:
        normalized_query = _normalize_query(query)
        if lead_thread_id is not None and independent_only:
            raise ThreadError("Choose workers or independent Threads, not both.", code="thread_page_invalid")
        if sum((project_id is not None, project_ids is not None, projectless)) > 1:
            raise ThreadError("Choose only one Project filter.", code="thread_page_invalid")
        if project_ids is not None:
            project_ids = tuple(sorted(set(project_ids)))
        project_ids_digest = (
            hashlib.sha256(json.dumps(project_ids).encode()).hexdigest() if project_ids is not None else None
        )
        if not 1 <= limit <= 100:
            raise ThreadError("Thread page is outside supported bounds.", code="thread_page_invalid")
        before: tuple[datetime, str] | None = None
        if cursor is not None:
            decoded = _decode_cursor(cursor, _ThreadCursor, code="thread_cursor_invalid")
            if (
                decoded.query != normalized_query
                or decoded.project_id != project_id
                or decoded.include_archived is not include_archived
                or decoded.archived_only is not archived_only
                or (
                    decoded.project_ids_digest != project_ids_digest
                    if decoded.project_ids_digest is not None
                    else decoded.project_ids != project_ids
                )
                or decoded.projectless != projectless
                or decoded.lead_thread_id != lead_thread_id
                or decoded.independent_only != independent_only
                or decoded.sort != sort
                or decoded.active_only != active_only
            ):
                raise ThreadError("Thread cursor belongs to another query.", code="thread_cursor_mismatch")
            before = (decoded.updated_at, decoded.thread_id)
        stored, total = await self._store.threads.list(
            query=normalized_query,
            project_id=project_id,
            include_archived=include_archived,
            archived_only=archived_only,
            project_ids=project_ids,
            projectless=projectless,
            lead_thread_id=lead_thread_id,
            independent_only=independent_only,
            sort=sort,
            thread_ids=active_thread_ids if active_only is True else None,
            exclude_thread_ids=active_thread_ids if active_only is False else (),
            before=before,
            limit=limit + 1,
        )
        visible = stored[:limit]
        activities: Mapping[str, RootActivityView] = {}
        if visible and self._root_activities is not None:
            activities = await self._root_activities(tuple(item.thread_id for item in visible))
        owners = await self._store.threads.worker_leads(tuple(item.thread_id for item in visible))
        summaries = tuple(
            [await self._summary(item, activity=activities.get(item.thread_id), owners=owners) for item in visible]
        )
        next_cursor = None
        if len(stored) > limit:
            last = visible[-1]
            next_cursor = _encode_cursor(
                _ThreadCursor(
                    query=normalized_query,
                    project_id=project_id,
                    include_archived=include_archived,
                    archived_only=archived_only,
                    active_only=active_only,
                    updated_at={
                        "updated": last.updated_at,
                        "activity": last.activity_at or last.created_at,
                        "touched": last.touched_at or last.created_at,
                    }[sort],
                    # A filter can cover many unavailable Projects; keep its cursor bounded.
                    project_ids_digest=project_ids_digest,
                    projectless=projectless,
                    lead_thread_id=lead_thread_id,
                    independent_only=independent_only,
                    sort=sort,
                    thread_id=last.thread_id,
                )
            )
        return ThreadPage(threads=summaries, total=total, next_cursor=next_cursor)

    async def lookup_threads(self, thread_ids: tuple[str, ...]) -> ThreadPage:
        """Bounded root lookup, including archived and off-page conversations; no history hydration."""
        if not 1 <= len(thread_ids) <= 100 or any(not item or len(item) > 80 for item in thread_ids):
            raise ThreadError("Thread lookup is outside supported bounds.", code="thread_page_invalid")
        stored, total = await self._store.threads.list(
            thread_ids=tuple(set(thread_ids)), include_archived=True, limit=100
        )
        activities = {} if self._root_activities is None else await self._root_activities(thread_ids)
        owners = await self._store.threads.worker_leads(thread_ids)
        return ThreadPage(
            threads=tuple(
                [await self._summary(item, activity=activities.get(item.thread_id), owners=owners) for item in stored]
            ),
            total=total,
        )

    async def detail(self, thread_id: str) -> ThreadDetail:
        thread = await self._required_thread(thread_id)
        summary = await self._summary(thread)
        continuation_id = None
        requests: tuple[DeferredRequestView, ...] = ()
        if thread.continuation is not None:
            continuation_id = thread.continuation.logical_digest
            if thread.read_model is not None:
                requests = _deferred_requests(thread.read_model.deferred_requests)
        actions: list[Literal["run", "respond", "wait", "steer", "cancel", "archive", "clear_context"]] = []
        if summary.root_activity.state is RootActivityState.inactive:
            if not thread.archived:
                if thread.continuation is None or thread.read_model is not None:
                    actions.append("respond" if requests else "run")
                actions.append("archive")
                if thread.parent_thread_id is None and thread.continuation is not None:
                    actions.append("clear_context")
        else:
            actions.extend(summary.root_activity.available_actions)
        return ThreadDetail(
            thread=summary,
            continuation_id=continuation_id,
            deferred_requests=requests,
            available_actions=tuple(actions),
        )

    async def transcript(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        turn_id: str | None = None,
    ) -> TranscriptPage:
        if not 1 <= limit <= 100:
            raise ThreadError("Transcript page is outside supported bounds.", code="thread_history_page_invalid")
        thread = await self._required_thread(thread_id)
        header = await self._inspection_header(thread)
        continuation_id = header.source_id
        if expected_continuation_id is not None and continuation_id != expected_continuation_id:
            raise ThreadError(
                "The selected Thread continuation changed before transcript projection.",
                code="thread_history_continuation_changed",
            )
        upper_bound = header.message_count
        if turn_id is not None and cursor is None:
            selected = await self._store.inspections.turns(thread_id, continuation_id, turn_id=turn_id, limit=1)
            selected_turn = TranscriptTurn.model_validate_json(selected[0]) if selected else None
            if selected_turn is None:
                raise ThreadError("Input is not in the selected history.", code="thread_history_input_missing")
            upper_bound = selected_turn.end_position
        position = max(0, upper_bound - limit)
        if cursor is not None:
            decoded = _decode_cursor(cursor, _TranscriptCursor, code="thread_history_cursor_invalid")
            if decoded.thread_id != thread_id or decoded.continuation_id != continuation_id:
                raise ThreadError(
                    "Transcript cursor belongs to another continuation.",
                    code="thread_history_cursor_mismatch",
                )
            if decoded.direction == "later":
                position = decoded.position
                upper_bound = min(header.message_count, position + limit)
            else:
                upper_bound = decoded.position
                position = max(0, upper_bound - limit)
        if upper_bound > header.message_count or position > header.message_count or min(position, upper_bound) < 0:
            raise ThreadError(
                "Transcript cursor is outside the selected history.", code="thread_history_cursor_invalid"
            )
        entries = await self._inspection_entries(thread_id, continuation_id, tuple(range(position, upper_bound)))
        next_cursor = None
        if position > 0:
            next_cursor = _encode_cursor(
                _TranscriptCursor(
                    thread_id=thread_id,
                    continuation_id=continuation_id,
                    position=position,
                )
            )
        visible_turns = tuple(
            TranscriptTurn.model_validate_json(value)
            for value in await self._store.inspections.turns(
                thread_id, continuation_id, lower=position, upper=upper_bound
            )
        )
        boundaries = sorted(
            {
                boundary
                for turn in visible_turns
                for boundary in (turn.input_position, turn.output_position)
                if boundary is not None and not position <= boundary < upper_bound
            }
        )
        # Conversation navigation skips the process pages of turns already visible.
        # Entry cursors remain available for each independent execution reader.
        earlier_turn_position = min((turn.input_position for turn in visible_turns), default=position)
        later_turn_position = max((turn.end_position for turn in visible_turns), default=upper_bound)
        return TranscriptPage(
            earlier_turns_cursor=_encode_cursor(
                _TranscriptCursor(thread_id=thread_id, continuation_id=continuation_id, position=earlier_turn_position)
            )
            if earlier_turn_position > 0
            else None,
            later_turns_cursor=_encode_cursor(
                _TranscriptCursor(
                    thread_id=thread_id,
                    continuation_id=continuation_id,
                    position=later_turn_position,
                    direction="later",
                )
            )
            if later_turn_position < header.message_count
            else None,
            turns=visible_turns,
            boundary_entries=await self._inspection_entries(thread_id, continuation_id, tuple(boundaries)),
            newer_cursor=_encode_cursor(
                _TranscriptCursor(
                    thread_id=thread_id, continuation_id=continuation_id, position=upper_bound, direction="later"
                )
            )
            if upper_bound < header.message_count
            else None,
            completion_version=0 if thread.completion is None else thread.completion.version,
            continuation_id=continuation_id,
            entries=entries,
            total=header.message_count,
            next_cursor=next_cursor,
        )

    async def transcript_inputs(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> TranscriptInputPage:
        if not 1 <= limit <= 100:
            raise ThreadError("Input page is outside supported bounds.", code="thread_history_page_invalid")
        thread = await self._required_thread(thread_id)
        header = await self._inspection_header(thread)
        continuation_id = header.source_id
        if expected_continuation_id is not None and expected_continuation_id != continuation_id:
            raise ThreadError("History changed before input projection.", code="thread_history_continuation_changed")
        position = 0
        if cursor:
            decoded = _decode_cursor(cursor, _InputCursor, code="thread_history_cursor_invalid")
            if decoded.thread_id != thread_id or decoded.continuation_id != continuation_id:
                raise ThreadError("Input cursor belongs to another history.", code="thread_history_cursor_mismatch")
            position = decoded.position
        if position > header.turn_count:
            raise ThreadError("Input cursor is outside history.", code="thread_history_cursor_invalid")
        end = min(header.turn_count, position + limit)
        turns = tuple(
            TranscriptTurn.model_validate_json(value)
            for value in await self._store.inspections.turns(thread_id, continuation_id, position=position, limit=limit)
        )
        if len(turns) != end - position:
            raise ThreadError("History changed before input projection.", code="thread_history_continuation_changed")
        return TranscriptInputPage(
            continuation_id=continuation_id,
            turns=turns,
            next_cursor=_encode_cursor(_InputCursor(thread_id=thread_id, continuation_id=continuation_id, position=end))
            if end < header.turn_count
            else None,
        )

    async def transcript_entry(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str,
        position: int,
    ) -> TranscriptEntry:
        thread = await self._required_thread(thread_id)
        header = await self._inspection_header(thread)
        continuation_id = header.source_id
        if continuation_id != expected_continuation_id:
            raise ThreadError(
                "The selected Thread continuation changed before transcript projection.",
                code="thread_history_continuation_changed",
            )
        if position < 0 or position >= header.message_count:
            raise ThreadError("Transcript position is invalid.", code="thread_history_position_invalid")
        return (await self._inspection_entries(thread_id, continuation_id, (position,)))[0]

    async def projects(self) -> tuple[ProjectSummary, ...]:
        source = await self._configurations.current()
        if source is None:
            raise ThreadError(
                "No accepted Harness UI configuration is selected.",
                code="configuration_not_accepted",
            )
        recency = await self._store.threads.project_recency()
        leads = await self._store.threads.project_leads()
        return tuple(
            ProjectSummary(
                project_id=project.id,
                name=project.name,
                position=project.position,
                roots=tuple(root.path for root in project.roots),
                last_active_at=recency.get(project.id),
                lead_thread_id=leads[project.id].thread_id if project.id in leads else None,
                lead_enabled=leads[project.id].enabled if project.id in leads else False,
                defaults=project.defaults,
            )
            for project in sorted(source.projects.values(), key=lambda item: (item.position, item.id))
        )

    async def _required_thread(self, thread_id: str) -> Thread:
        thread = await self._store.threads.get(thread_id)
        if thread is None:
            raise ThreadError("Thread does not exist.", code="thread_missing")
        return thread

    async def _summary(
        self,
        thread: Thread,
        *,
        activity: RootActivityView | None = None,
        owners: Mapping[str, str] | None = None,
    ) -> ThreadSummary:
        if owners is None:
            owners = await self._store.threads.worker_leads((thread.thread_id,))
        if activity is None:
            activity = _ROOT_INACTIVE
            if thread.parent_thread_id is None and self._root_activity is not None:
                activity = await self._root_activity(thread.thread_id)
        goal = None if thread.read_model is None else thread.read_model.goal
        if goal is not None and goal.active and activity.state is RootActivityState.inactive:
            # An active checkpoint left by an interrupted process is not liveness.
            pending = thread.read_model is not None and thread.read_model.deferred_requests is not None
            goal = goal.model_copy(update={"status": "suspended" if pending else "unverified_stop"})
        return ThreadSummary(
            thread_id=thread.thread_id,
            lead_thread_id=owners.get(thread.thread_id),
            parent_thread_id=thread.parent_thread_id,
            created_at=thread.created_at,
            updated_at=thread.updated_at,
            metadata_version=thread.metadata_version,
            title=thread.title,
            excerpt=thread.excerpt,
            activity_at=thread.activity_at,
            touched_at=thread.touched_at,
            archived=thread.archived,
            configuration=_configuration(thread.configuration),
            continuation_state="initial" if thread.continuation is None else "selected",
            root_activity=activity,
            completion=thread.completion,
            goal=goal,
        )

    async def context_usage(self, thread_id: str) -> ContextUsageView:
        thread = await self._store.threads.get(thread_id)
        if thread is None:
            raise ThreadError("Session does not exist.", code="thread_missing")
        if thread.continuation is None:
            return ContextUsageView(thread_id=thread_id)
        inspection = await self.inspection(thread)
        assert inspection.run_composition is not None
        composition = await self._store.objects.read_model(inspection.run_composition, ResolvedRunComposition)
        latest = inspection.latest_request_tokens
        observed = await self._store.usage.latest_root_request(thread_id=thread_id)
        if observed is not None and not inspection.context_empty:
            latest = observed.request_usage.input_tokens + observed.request_usage.output_tokens
        model = composition.root.model
        from a13n_harness_ui.model_thinking import summarize_thinking

        thinking = model.thinking_override if model.thinking_override is not None else model.settings.get("thinking")
        return ContextUsageView(
            thread_id=thread_id,
            latest_request_tokens=latest,
            context_window=(
                None if model.model_characteristics is None else model.model_characteristics.context_window_tokens
            ),
            model_id=model.model_id,
            thinking=thinking if isinstance(thinking, (str, bool)) else None,
            thinking_summary=summarize_thinking(model.route, model.settings),
        )

    async def inspection(self, thread: Thread) -> ThreadInspection:
        header = await self._inspection_header(thread)
        return ThreadInspection.model_validate_json(header.metadata_json)

    async def _inspection_entries(
        self, thread_id: str, source_id: str, positions: tuple[int, ...]
    ) -> tuple[TranscriptEntry, ...]:
        values = await self._store.inspections.entries(thread_id, source_id, positions)
        return await to_thread.run_sync(lambda: tuple(TranscriptEntry.model_validate_json(value) for value in values))

    async def _inspection_header(self, thread: Thread) -> InspectionHeader:
        source_id = (
            thread.continuation.logical_digest
            if thread.continuation
            else f"initial:{thread.initial_state.logical_digest}"
        )
        if (header := await self._store.inspections.header(thread.thread_id, source_id)) is not None:
            return header
        # Only callers building the same Thread wait together. No native history
        # remains cached, and idle per-Thread locks disappear with the last waiter.
        lock, users = self._inspection_loads.get(thread.thread_id, (Lock(), 0))
        self._inspection_loads[thread.thread_id] = (lock, users + 1)
        try:
            async with lock:
                if (header := await self._store.inspections.header(thread.thread_id, source_id)) is not None:
                    return header
                if thread.continuation is None:
                    stored = await self._store.objects.read_model(thread.initial_state, StoredThreadInitialState)
                else:
                    stored = await self._store.objects.read_model(thread.continuation, StoredContinuation)
                data = await to_thread.run_sync(build_thread_inspection, thread, stored)
                if not await self._store.inspections.publish(thread.thread_id, source_id, data):
                    raise ThreadError("The selected history changed.", code="thread_history_continuation_changed")
                return InspectionHeader(source_id, len(data.entries), len(data.turns), data.metadata_json)
        finally:
            _, users = self._inspection_loads[thread.thread_id]
            if users == 1:
                del self._inspection_loads[thread.thread_id]
            else:
                self._inspection_loads[thread.thread_id] = (lock, users - 1)


def project_task(task: Task) -> TaskView:
    return TaskView(
        task_id=task.id,
        version=task.version,
        subject=task.subject,
        active_form=task.active_form,
        status=task.status,
        owner=task.owner,
        blocks=task.blocks,
        blocked_by=task.blocked_by,
    )


def project_task_page(state: TaskState | None, continuation_id: str | None = None) -> TaskPage:
    if state is None:
        return TaskPage(continuation_id=continuation_id, available=False)
    ordered = sorted(state.tasks.values(), key=lambda item: int(item.id.removeprefix("task-")))
    return TaskPage(
        continuation_id=continuation_id,
        version=state.version,
        tasks=tuple(project_task(task) for task in ordered[:256]),
        total=len(ordered),
        omitted=max(0, len(ordered) - 256),
    )


def project_note_page(values: Mapping[str, str], continuation_id: str | None = None) -> NotePage:
    visible: list[NoteView] = []
    size = 0
    for key, value in sorted(values.items()):
        cost = len(key.encode()) + len(value.encode())
        if len(visible) >= 256 or size + cost > 256 * 1024:
            break
        visible.append(NoteView(key=key, value=value))
        size += cost
    return NotePage(
        continuation_id=continuation_id,
        notes=tuple(visible),
        total=len(values),
        omitted=len(values) - len(visible),
    )


def project_working_state(working: WorkingState, continuation_id: str | None = None) -> tuple[TaskPage, NotePage]:
    """Bound the same work details for saved inspection and current observation."""
    return (
        project_task_page(working.tasks if working.task_mode == "embedded" else None, continuation_id),
        project_note_page(working.notes, continuation_id),
    )


def build_thread_inspection(thread: Thread, stored: StoredContinuation | StoredThreadInitialState) -> InspectionData:
    """Project once off-loop, then read bounded indexed rows across process restarts."""
    state = stored.harness_state
    if state.thread_id != thread.thread_id:
        raise ThreadError("Thread state belongs to another Thread.", code="thread_continuation_incompatible")
    display = stored.display_history if isinstance(stored, StoredContinuation) else None
    history = display.messages if display is not None else state.message_history
    completed = display.completed_responses if display is not None else ()
    continuation_id = thread.continuation.logical_digest if thread.continuation else None
    notes = NotePage(continuation_id=continuation_id)
    tasks = TaskPage(continuation_id=continuation_id)
    entry = state.agent_context_state.entries.get(WORKING_STATE_CAPABILITY_ID)
    if entry is not None and continuation_id is not None:
        working = WorkingState.model_validate(entry.data)
        tasks, notes = project_working_state(working, continuation_id)
    metadata = ThreadInspection(
        context_empty=not state.message_history,
        run_composition=stored.run_composition if isinstance(stored, StoredContinuation) else None,
        latest_request_tokens=next(
            (
                message.usage.total_tokens
                for message in reversed(state.message_history)
                if isinstance(message, ModelResponse) and message.usage.total_tokens > 0
            ),
            None,
        ),
        notes=notes,
        tasks=tasks,
    )
    turns = _transcript_turns(history, completed)
    return InspectionData(
        metadata_json=metadata.model_dump_json(),
        entries=tuple(
            _message_entry(position, message, thread=thread).model_dump_json()
            for position, message in enumerate(history)
        ),
        turns=tuple(
            InspectionTurn(turn.turn_id, turn.input_position, turn.end_position, turn.model_dump_json())
            for turn in turns
        ),
    )


def _additional_input_count(messages: tuple[ModelMessage, ...]) -> int:
    identities: set[tuple[str, str | int]] = set()
    for position, message in enumerate(messages):
        metadata = message.metadata or {}
        if (
            not isinstance(message, ModelRequest)
            or "a13n.steering-run" not in metadata
            or metadata.get("a13n.context")
            or metadata.get("a13n.steering-source") in {"background_process", "async_subagent"}
        ):
            continue
        visible = [
            projected[1]
            for part in message.parts
            if isinstance(part, UserPromptPart)
            for item in user_prompt_content(part)
            if (projected := project_input_content(item)) is not None
            and projected[1].display
            and (projected[1].model_extra or {}).get("a13n.steering-source")
            not in {"background_process", "async_subagent"}
        ]
        if not visible:
            continue
        identity = metadata.get("a13n.steering-input")
        source = next((part.source_id for part in visible if part.source_id), None)
        if isinstance(identity, str):
            identities.add(("input", identity))
        elif source:
            identities.add(("source", source))
        else:
            # Legacy steering has no input identity; each request is one submission.
            identities.add(("request", position))
    return len(identities)


def _transcript_turns(
    history: tuple[ModelMessage, ...],
    completed: tuple[int, ...] = (),
) -> tuple[TranscriptTurn, ...]:
    """Project input boundaries from retained native metadata, never text matching."""
    inputs: list[tuple[int, str, str]] = []
    for position, message in enumerate(history):
        metadata = message.metadata or {}
        if not isinstance(message, ModelRequest) or "a13n.steering-run" in metadata or metadata.get("a13n.context"):
            continue
        content = [
            item for part in message.parts if isinstance(part, UserPromptPart) for item in user_prompt_content(part)
        ]
        preview = input_excerpt(content)
        if not preview:
            continue
        source = next(
            (
                projected[1].source_id
                for item in content
                if (projected := project_input_content(item)) is not None
                and projected[1].display
                and projected[1].source_id
            ),
            None,
        )
        inputs.append((position, source or f"entry:{position}", excerpt_text(preview, 512)))
    turns: list[TranscriptTurn] = []
    completed_set = set(completed)
    for index, (position, identity, preview) in enumerate(inputs):
        end = inputs[index + 1][0] if index + 1 < len(inputs) else len(history)
        # Completion is recorded only by successful saved execution. A later
        # resume/checkpoint makes an old result provisional, not a completed turn.
        final_position = end - 1 if end - 1 in completed_set else None
        messages = history[position:end]
        closing = messages[-1]
        # A readable closing response is not proof of successful execution.
        # Legacy histories lack completion markers, but must not lose their answer
        # when execution details are manually collapsed or paged separately.
        output_position = (
            end - 1
            if isinstance(closing, ModelResponse)
            and closing.state == "complete"
            and not (closing.metadata or {}).get("a13n.context")
            and (closing.metadata or {}).get("keep") != "compact"
            and not any(isinstance(part, (ToolCallPart, NativeToolCallPart)) for part in closing.parts)
            and any(isinstance(part, TextPart) and part.content.strip() for part in closing.parts)
            else None
        )
        if final_position is not None:
            output_position = final_position
        output_preview = (
            excerpt_text(" ".join(part.content for part in closing.parts if isinstance(part, TextPart)), 512)
            if output_position is not None
            else None
        )
        turns.append(
            TranscriptTurn(
                turn_id=identity,
                input_position=position,
                end_position=end,
                final_position=final_position,
                output_position=output_position,
                output_preview=output_preview,
                preview=preview,
                timestamp=history[position].timestamp,
                tool_count=sum(
                    isinstance(part, (ToolCallPart, NativeToolCallPart))
                    for message in messages
                    for part in message.parts
                ),
                steering_count=_additional_input_count(messages),
            )
        )
    return tuple(turns)


def _configuration(value: ThreadConfiguration) -> ThreadConfigurationView:
    return ThreadConfigurationView(
        version=value.version,
        project_id=value.project_id,
        agent_source=AgentSourceView.from_stored(value.agent_source),
        default_model_id=value.default_model_id,
        local_roots=value.local_roots,
        environment_profile_id=value.environment_profile_id,
        environment_bindings=value.environment_bindings,
        default_environment=value.default_environment,
        harness_plugin_ids=value.harness_plugin_ids,
        environment_run_extension_ids=value.environment_run_extension_ids,
        mcp_server_ids=value.mcp_server_ids,
    )


def _normalize_query(query: str | None) -> str | None:
    if query is None:
        return None
    if len(query) > 512:
        raise ThreadError("Thread query is too large.", code="thread_query_invalid")
    normalized = query.strip().casefold()
    return normalized or None


def _encode_cursor(value: BaseModel) -> str:
    payload = value.model_dump_json().encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor[CursorT: BaseModel](
    value: str,
    model: type[CursorT],
    *,
    code: str,
) -> CursorT:
    if not value or len(value) > _MAX_CURSOR_BYTES * 2:
        raise ThreadError("Cursor is invalid.", code=code)
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = base64.b64decode(padded, altchars=b"-_", validate=True)
        if len(payload) > _MAX_CURSOR_BYTES:
            raise ValueError("cursor is too large")
        return model.model_validate_json(payload, strict=True)
    except (ValueError, UnicodeDecodeError, ValidationError) as exc:
        raise ThreadError("Cursor is invalid.", code=code) from exc


def _message_entry(position: int, message: ModelMessage, *, thread: Thread | None = None) -> TranscriptEntry:
    if isinstance(message, ModelRequest):
        parts = tuple(part for source in message.parts for part in _request_parts(source))
        if (message.metadata or {}).get("a13n.context") == "handoff":
            # The owned handoff request contains the summary first, followed by
            # internal restoration instructions. Retained user requests are separate.
            summary_seen = False
            projected: list[TranscriptPart] = []
            for part in parts:
                if part.kind == "user" and not summary_seen:
                    projected.append(
                        part.model_copy(
                            update={
                                "metadata": ContentMetadata.from_native(
                                    {
                                        **part.metadata.model_dump(),
                                        "a13n.context": "handoff",
                                    }
                                )
                            }
                        )
                    )
                    summary_seen = True
                else:
                    projected.append(part.model_copy(update={"metadata": ContentMetadata(display=False)}))
            parts = tuple(projected)
        return TranscriptEntry(
            position=position,
            message_kind="request",
            timestamp=message.timestamp,
            parts=parts,
        )
    if isinstance(message, ModelResponse):
        if (message.metadata or {}).get("keep") == "compact":
            return TranscriptEntry(
                position=position,
                message_kind="response",
                timestamp=message.timestamp,
                parts=tuple(
                    _response_part(part).model_copy(
                        update={
                            **({"text": part.content, "text_truncated": False} if isinstance(part, TextPart) else {}),
                            "metadata": ContentMetadata.from_native(
                                {
                                    "a13n.context": "compaction",
                                    **(
                                        {"operation_id": message.metadata["operation_id"]}
                                        if message.metadata and "operation_id" in message.metadata
                                        else {}
                                    ),
                                }
                            ),
                        }
                    )
                    for part in message.parts
                ),
            )
        return TranscriptEntry(
            position=position,
            message_kind="response",
            timestamp=message.timestamp,
            parts=tuple(
                _response_part(part).model_copy(
                    update={
                        "comment_target": SavedOutputTarget(
                            producing_thread_id=thread.thread_id,
                            source_id=thread.continuation.logical_digest,
                            location=RootOutputLocation(message=position, part=index),
                        )
                    }
                )
                if (
                    isinstance(part, TextPart)
                    and thread is not None
                    and thread.parent_thread_id is None
                    and thread.continuation is not None
                )
                else _response_part(part)
                for index, part in enumerate(message.parts)
            ),
        )
    raise ThreadError("Thread history contains an unsupported message.", code="thread_history_invalid")


def _request_parts(part: object) -> tuple[TranscriptPart, ...]:
    if isinstance(part, SystemPromptPart):
        return (TranscriptPart(kind="system", text=_bounded_text(part.content)),)
    if isinstance(part, UserPromptPart):
        return tuple(
            TranscriptPart(
                kind="media" if metadata.media else "user",
                text=content
                if isinstance(content, str) and (metadata.model_extra or {}).get("a13n.context") == "handoff"
                else _bounded_text(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)),
                metadata=metadata.model_copy(deep=True),
            )
            for item in user_prompt_content(part)
            if (projected := project_input_content(item)) is not None
            for content, metadata in (projected,)
        )
    if isinstance(part, ToolReturnPart):
        value, omitted = _bounded_json(part.content)
        return (
            TranscriptPart(
                kind="tool_result",
                tool_name=part.tool_name,
                tool_call_id=part.tool_call_id,
                outcome=part.outcome,
                applied_edit=applied_edit(part),
                value=value,
                value_omitted=omitted,
            ),
        )
    if isinstance(part, RetryPromptPart):
        text = part.content if isinstance(part.content, str) else "Tool input validation failed."
        return (
            TranscriptPart(
                kind="retry",
                text=_bounded_text(text),
                text_truncated=len(text) > _MAX_TEXT,
                tool_name=part.tool_name,
                tool_call_id=part.tool_call_id,
            ),
        )
    return (TranscriptPart(kind="other", text=type(part).__name__),)


def _response_part(part: object) -> TranscriptPart:
    if isinstance(part, TextPart):
        return TranscriptPart(
            kind="assistant", text=_bounded_text(part.content), text_truncated=len(part.content) > _MAX_TEXT
        )
    if isinstance(part, ThinkingPart):
        return TranscriptPart(kind="thinking", text=_bounded_text(part.content))
    if isinstance(part, NativeToolReturnPart):
        value, omitted = _bounded_json(part.content)
        return TranscriptPart(
            kind="tool_result",
            tool_name=part.tool_name,
            tool_call_id=part.tool_call_id,
            provider=part.provider_name or "provider",
            outcome=part.outcome,
            value=value,
            value_omitted=omitted,
        )
    if isinstance(part, (ToolCallPart, NativeToolCallPart)):
        value, omitted = _bounded_json(part.args)
        return TranscriptPart(
            kind="tool_call",
            provider=(part.provider_name or "provider") if isinstance(part, NativeToolCallPart) else None,
            tool_name=part.tool_name,
            tool_call_id=part.tool_call_id,
            value=value,
            value_omitted=omitted,
        )
    return TranscriptPart(kind="other", text=type(part).__name__)


def _bounded_text(value: str) -> str:
    if len(value) <= _MAX_TEXT:
        return value
    return value[: _MAX_TEXT - 23] + "\n...[content truncated]"


def _bounded_json(value: object) -> tuple[JsonValue | None, bool]:
    try:
        projected = _JSON_ADAPTER.validate_python(value)
        encoded = json.dumps(projected, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, ValidationError):
        return None, True
    if len(encoded) > _MAX_JSON_BYTES:
        return None, True
    return projected, False


def _deferred_requests(value: object | None) -> tuple[DeferredRequestView, ...]:
    if value is None:
        return ()
    if not isinstance(value, DeferredToolRequests):
        raise ThreadError("Stored deferred requests are invalid.", code="thread_continuation_incompatible")
    projected: list[DeferredRequestView] = []
    for request in value.calls:
        projected.append(_deferred_request(value, request, kind="external"))
    for request in value.approvals:
        projected.append(_deferred_request(value, request, kind="approval"))
    return tuple(projected)


def _deferred_request(
    requests: DeferredToolRequests,
    request: ToolCallPart,
    *,
    kind: Literal["approval", "external"],
) -> DeferredRequestView:
    arguments, arguments_omitted = _bounded_json(request.args)
    metadata, metadata_omitted = _bounded_metadata(requests.metadata.get(request.tool_call_id))
    return DeferredRequestView(
        request_id=request.tool_call_id,
        kind=kind,
        tool_name=request.tool_name,
        arguments=arguments,
        arguments_omitted=arguments_omitted,
        metadata=metadata,
        metadata_omitted=metadata_omitted,
    )


def _bounded_metadata(value: object | None) -> tuple[dict[str, JsonValue] | None, bool]:
    if value is None:
        return None, False
    projected, omitted = _bounded_json(value)
    if omitted or not isinstance(projected, dict):
        return None, True
    return projected, False


__all__ = ["RootActivityBatchLookup", "RootActivityLookup", "ThreadProjectionService"]
