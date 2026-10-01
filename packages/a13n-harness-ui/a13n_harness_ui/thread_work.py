"""Current Thread work observations, separate from saved continuation inspection."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from a13n_harness import HarnessState
from a13n_harness.capabilities.working_state import (
    WORKING_STATE_CAPABILITY_ID,
    TaskState,
    WorkingState,
    WorkingStateObservation,
)
from anyio import to_thread

from a13n_harness_ui.errors import ThreadError
from a13n_harness_ui.live import HarnessUiSummaryHub
from a13n_harness_ui.storage import LocalStore, Thread
from a13n_harness_ui.storage.work import WorkData
from a13n_harness_ui.surfaces import (
    ChildStatusCounts,
    NotePage,
    NoteWorkSummary,
    SurfaceModel,
    TaskPage,
    TaskWorkSummary,
    ThreadWork,
)
from a13n_harness_ui.thread_projection import project_note_page, project_task, project_task_page


class WorkSummary(SurfaceModel):
    tasks: TaskWorkSummary
    notes: NoteWorkSummary


def project_work(
    state: WorkingState | None,
    tasks: TaskState | None,
    *,
    notes_version: int | None = None,
    include: tuple[Literal["tasks", "notes"], ...] = (),
) -> WorkSummary:
    task_summary = TaskWorkSummary(available=False, source="unavailable")
    note_summary = NoteWorkSummary(available=state is not None)
    if state is not None:
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
            version=notes_version,
            total=len(notes),
            page=project_note_page(notes) if "notes" in include else None,
        )
    return WorkSummary(tasks=task_summary, notes=note_summary)


def build_work_projection(state: HarnessState) -> WorkData:
    """Retain complete counts but only bounded details, never display history."""
    entry = state.agent_context_state.get(WORKING_STATE_CAPABILITY_ID)
    working = WorkingState.model_validate(entry.data) if entry is not None else None
    summary = project_work(working, working.tasks if working is not None else None, include=("tasks", "notes"))
    return WorkData(
        summary_json=summary.model_dump_json(exclude={"tasks": {"page"}, "notes": {"page"}}),
        tasks_json=summary.tasks.page.model_dump_json() if summary.tasks.page is not None else None,
        notes_json=summary.notes.page.model_dump_json() if summary.notes.page is not None else None,
    )


@dataclass(frozen=True, slots=True)
class _RootWork:
    base_continuation_id: str | None
    observation: WorkingStateObservation


class ThreadWorkService:
    """Retain active root observations; saved queries use small persistent projections."""

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

    async def _saved_work(self, thread: Thread, include: tuple[Literal["tasks", "notes"], ...]) -> WorkSummary:
        data = await self._store.work.read(thread.thread_id, thread.continuation or thread.initial_state, include)
        if data is None:
            return project_work(None, None)

        def decode() -> WorkSummary:
            summary = WorkSummary.model_validate_json(data.summary_json)
            return WorkSummary(
                tasks=summary.tasks.model_copy(
                    update={
                        "page": TaskPage.model_validate_json(data.tasks_json) if data.tasks_json is not None else None
                    }
                ),
                notes=summary.notes.model_copy(
                    update={
                        "page": NotePage.model_validate_json(data.notes_json) if data.notes_json is not None else None
                    }
                ),
            )

        return await to_thread.run_sync(decode)

    async def snapshot(self, thread_id: str, include: tuple[Literal["tasks", "notes"], ...] = ()) -> ThreadWork:
        # Capture the hint boundary before I/O. A later hint always requires a
        # follow-up read; sections intentionally do not claim transactionality.
        cursor = self._summary.cursor
        thread = await self._store.threads.get(thread_id)
        if thread is None:
            raise ThreadError("Thread does not exist.", code="thread_missing")
        live = self._live.get(thread_id)
        current = live.observation if live is not None else None
        work = (
            project_work(current.state, current.tasks, notes_version=current.notes_version, include=include)
            if current is not None
            else await self._saved_work(thread, include)
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
            source="live" if current is not None else "saved" if work.notes.available else "unavailable",
            run_id=current.run_id if current is not None else None,
            revision=current.revision if current is not None else None,
            base_continuation_id=live.base_continuation_id if live is not None else None,
            continuation_id=thread.continuation.logical_digest if thread.continuation is not None else None,
            tasks=work.tasks,
            notes=work.notes,
            children=children,
        )
