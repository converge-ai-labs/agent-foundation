"""Current Thread work observations, separate from saved continuation inspection."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from a13n_harness.capabilities.working_state import (
    WORKING_STATE_CAPABILITY_ID,
    WorkingState,
    WorkingStateObservation,
)

from a13n_harness_ui.errors import ThreadError
from a13n_harness_ui.live import HarnessUiSummaryHub
from a13n_harness_ui.storage import LocalStore, StoredContinuation, StoredThreadInitialState, Thread
from a13n_harness_ui.surfaces import ChildStatusCounts, NoteWorkSummary, TaskWorkSummary, ThreadWork
from a13n_harness_ui.thread_projection import project_note_page, project_task, project_task_page


@dataclass(frozen=True, slots=True)
class _RootWork:
    base_continuation_id: str | None
    observation: WorkingStateObservation


class ThreadWorkService:
    """Retain only active root observations and a bounded immutable saved cache."""

    def __init__(
        self,
        store: LocalStore,
        summary: HarnessUiSummaryHub,
        active_children: Callable[[], Awaitable[frozenset[str]]],
    ) -> None:
        self._store = store
        self._summary = summary
        self._active_children = active_children
        self._live: dict[str, _RootWork] = {}
        self._saved: OrderedDict[str, WorkingState | None] = OrderedDict()

    def observe(
        self, thread_id: str, observation: WorkingStateObservation, *, base_continuation_id: str | None
    ) -> None:
        prior = self._live.get(thread_id)
        previous = prior.observation if prior is not None else None
        self._live[thread_id] = _RootWork(base_continuation_id, observation)
        sections: list[Literal["tasks", "notes", "children"]] = []
        if previous is None or previous.run_id != observation.run_id or previous.tasks != observation.tasks:
            sections.append("tasks")
        if (
            previous is None
            or previous.run_id != observation.run_id
            or previous.notes_version != observation.notes_version
        ):
            sections.append("notes")
        self._summary.publish_nowait(
            kind="thread_work",
            thread_id=thread_id,
            run_id=observation.run_id,
            work_revision=observation.revision,
            work_sections=tuple(sections),
        )

    async def finish(self, thread_id: str, run_id: str) -> None:
        current = self._live.get(thread_id)
        if current is not None and current.observation.run_id == run_id:
            del self._live[thread_id]
            await self._summary.publish(
                kind="thread_work",
                thread_id=thread_id,
                work_sections=("tasks", "notes"),
            )

    async def _saved_state(self, thread: Thread) -> WorkingState | None:
        ref = thread.continuation or thread.initial_state
        key = ref.logical_digest
        if key in self._saved:
            self._saved.move_to_end(key)
            return self._saved[key]
        stored = (
            await self._store.objects.read_model(ref, StoredContinuation)
            if thread.continuation is not None
            else await self._store.objects.read_model(ref, StoredThreadInitialState)
        )
        entry = stored.harness_state.agent_context_state.entries.get(WORKING_STATE_CAPABILITY_ID)
        state = WorkingState.model_validate(entry.data) if entry is not None else None
        self._saved[key] = state
        while len(self._saved) > 16:
            self._saved.popitem(last=False)
        return state

    async def snapshot(self, thread_id: str, include: tuple[Literal["tasks", "notes"], ...] = ()) -> ThreadWork:
        # Capture the hint boundary before I/O. A later hint always requires a
        # follow-up read; sections intentionally do not claim transactionality.
        cursor = self._summary.cursor
        thread = await self._store.threads.get(thread_id)
        if thread is None:
            raise ThreadError("Thread does not exist.", code="thread_missing")
        live = self._live.get(thread_id)
        current = live.observation if live is not None else None
        state = current.state if current is not None else await self._saved_state(thread)
        tasks = current.tasks if current is not None else state.tasks if state is not None else None
        task_summary = TaskWorkSummary(available=False, source="unavailable")
        note_summary = NoteWorkSummary(available=state is not None)
        if state is not None:
            # Provider observations remain outside the persisted WorkingState.
            notes = state.notes
            values = tuple(tasks.tasks.values()) if tasks is not None else ()
            task_summary = TaskWorkSummary(
                available=tasks is not None,
                source="unavailable"
                if tasks is None
                else "embedded"
                if state.task_mode == "embedded"
                else "provider_observed",
                version=tasks.version if tasks is not None else None,
                total=len(values),
                pending=sum(task.status == "pending" for task in values),
                in_progress=sum(task.status == "in_progress" for task in values),
                completed=sum(task.status == "completed" for task in values),
                active=next((project_task(task) for task in values if task.status == "in_progress"), None),
                page=project_task_page(tasks) if "tasks" in include else None,
            )
            note_summary = NoteWorkSummary(
                version=current.notes_version if current is not None else None,
                total=len(notes),
                page=project_note_page(notes) if "notes" in include else None,
            )
        counts, running = await self._store.child_executions.status_counts_for_roots((thread_id,))
        statuses = counts.get(thread_id, {})
        running_ids = running.get(thread_id, ())
        active_ids = await self._active_children()
        active = sum(execution_id in active_ids for execution_id in running_ids)
        children = ChildStatusCounts(
            running=statuses.get("running", 0),
            succeeded=statuses.get("succeeded", 0),
            failed=statuses.get("failed", 0),
            cancelled=statuses.get("cancelled", 0),
            lost=statuses.get("lost", 0),
            active=active,
            unavailable=len(running_ids) - active,
        )
        return ThreadWork(
            thread_id=thread_id,
            epoch=cursor.epoch,
            sequence=cursor.sequence,
            source="live" if current is not None else "saved" if state is not None else "unavailable",
            run_id=current.run_id if current is not None else None,
            revision=current.revision if current is not None else None,
            base_continuation_id=live.base_continuation_id if live is not None else None,
            continuation_id=thread.continuation.logical_digest if thread.continuation is not None else None,
            tasks=task_summary,
            notes=note_summary,
            children=children,
        )
