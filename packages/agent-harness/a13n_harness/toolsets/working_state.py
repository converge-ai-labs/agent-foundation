"""Working State tool schemas and concrete run-local task/note semantics."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from copy import deepcopy
from secrets import token_urlsafe
from typing import Literal, TypedDict

from pydantic import JsonValue, ValidationError
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.events import TaskChangedPayload, TaskEventProjection, emit_harness_event

from ._instructions import InstructionFunctionToolset, tool_instruction
from ._results import ToolError, ToolFailure

_TASK_INSTRUCTION = tool_instruction("task-manager")
_NOTE_INSTRUCTION = tool_instruction("note")


class TaskProjection(TypedDict):
    id: str
    subject: str
    description: str
    active_form: str | None
    status: Literal["pending", "in_progress", "completed"]
    owner: str | None
    blocks: list[str]
    blocked_by: list[str]
    metadata: dict[str, JsonValue]


class TaskSuccess(TypedDict):
    ok: Literal[True]
    task: TaskProjection


class TaskListSuccess(TypedDict):
    ok: Literal[True]
    tasks: list[TaskProjection]


type TaskToolResult = TaskSuccess | ToolFailure
type TaskListToolResult = TaskListSuccess | ToolFailure


class NoteMutationSuccess(TypedDict):
    ok: Literal[True]
    key: str
    present: bool


class NoteKeysSuccess(TypedDict):
    ok: Literal[True]
    keys: list[str]


class NoteValueSuccess(TypedDict):
    ok: Literal[True]
    key: str
    value: str


type NoteToolResult = NoteMutationSuccess | ToolFailure
type NoteGetToolResult = NoteKeysSuccess | NoteValueSuccess | ToolFailure


class WorkingStateToolset:
    """Own model-facing task/note execution over one run-local coherent state."""

    def __init__(self, *, owner: AbstractCapability[AgentContext], context: AgentContext, state, configuration) -> None:
        self._owner = owner
        self._context = context
        self._state = state.model_copy(deep=True)
        self._configuration = configuration.model_copy(deep=True)
        self._cell = None
        self._provider = None
        self._task_binding_resolved = False
        self._state_lock = asyncio.Lock()
        self._last_task_observation = None

    def get_toolset(self, *, tasks: bool, notes: bool) -> FunctionToolset[AgentContext] | None:
        tools = []
        instructions: list[str] = []
        if tasks:
            tools.extend((self.task_create, self.task_get, self.task_list, self.task_update))
            instructions.append(_TASK_INSTRUCTION)
        if notes:
            tools.extend((self.note, self.note_get))
            instructions.append(_NOTE_INSTRUCTION)
        return (
            InstructionFunctionToolset(
                tools=tools,
                id="a13n-working-state-tools",
                instructions=instructions,
            )
            if tools
            else None
        )

    async def embedded_task_cell(self, ctx: RunContext[AgentContext]):
        """Return the validated parent-owned embedded cell to the owning Capability."""
        from a13n_harness.capabilities.working_state import EmbeddedTaskStateCell

        await self.ensure_bound(ctx)
        if not self._configuration.tasks_enabled or self._configuration.task_mode != "embedded":
            return None
        cell = self._require_cell()
        if not isinstance(cell, EmbeddedTaskStateCell):
            raise DefinitionError(
                "Embedded task sharing requires the Harness embedded task cell.",
                code="task_state_type_mismatch",
            )
        return cell

    async def context_snapshot(self, ctx: RunContext[AgentContext]):
        await self.ensure_bound(ctx)
        return await self._task_snapshot() if self._configuration.tasks_enabled else None

    def note_keys(self) -> tuple[str, ...]:
        return tuple(self._state.notes)

    async def task_create(
        self,
        ctx: RunContext[AgentContext],
        subject: str,
        description: str,
        active_form: str | None = None,
        blocked_by: Sequence[str] = (),
        blocks: Sequence[str] = (),
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> TaskToolResult:
        from a13n_harness.capabilities.working_state import CreateTask

        await self.ensure_bound(ctx)
        try:
            request = CreateTask(
                subject=subject,
                description=description,
                active_form=active_form,
                blocked_by=tuple(blocked_by),
                blocks=tuple(blocks),
                metadata=dict(metadata or {}),
            )
        except ValidationError:
            return {"ok": False, "error": {"code": "task_request_invalid"}}
        return await self._task_result("create", request)

    async def task_get(self, ctx: RunContext[AgentContext], task_id: str) -> TaskToolResult:
        from a13n_harness.capabilities.working_state import TaskStateError, _require_task

        await self.ensure_bound(ctx)
        try:
            task = _require_task((await self._task_snapshot()).tasks, task_id)
            return {"ok": True, "task": _project_task(task)}
        except TaskStateError as exc:
            return _task_error(exc)

    async def task_list(self, ctx: RunContext[AgentContext]) -> TaskListToolResult:
        from a13n_harness.capabilities.working_state import _task_sequence

        await self.ensure_bound(ctx)
        state = await self._task_snapshot()
        tasks = sorted(state.tasks.values(), key=lambda item: _task_sequence(item.id))
        return {"ok": True, "tasks": [_project_task(task) for task in tasks]}

    async def task_update(
        self,
        ctx: RunContext[AgentContext],
        task_id: str,
        status: Literal["pending", "in_progress", "completed"] | None = None,
        subject: str | None = None,
        description: str | None = None,
        active_form: str | None = None,
        add_blocks: Sequence[str] = (),
        add_blocked_by: Sequence[str] = (),
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> TaskToolResult:
        from a13n_harness.capabilities.working_state import (
            TaskMutation,
            TaskStateError,
            _mutation_is_empty,
            _require_task,
        )

        await self.ensure_bound(ctx)
        try:
            mutation = TaskMutation(
                status=status,
                subject=subject,
                description=description,
                active_form=active_form,
                clear_active_form=status == "completed",
                add_blocks=tuple(add_blocks),
                add_blocked_by=tuple(add_blocked_by),
                metadata=dict(metadata) if metadata is not None else None,
            )
            cell = self._require_cell()
            snapshot = await cell.snapshot()
            current = _require_task(snapshot.tasks, task_id)
            claim = (
                current.status == "in_progress"
                or status in {"in_progress", "completed"}
                or not _mutation_is_empty(mutation)
            )
            if status == "in_progress":
                mutation = mutation.model_copy(update={"status": None})
            if _mutation_is_empty(mutation) and not claim:
                task = current
            else:
                task = await cell.mutate(task_id, mutation, current.revision, claim=claim)
            committed = await cell.snapshot()
            if committed != snapshot:
                await self._observe_provider(snapshot)
                await self._emit_provider_changes(snapshot)
                await self._observe_provider(committed)
            reason: Literal["updated", "claimed", "completed"]
            if current.status != "completed" and task.status == "completed":
                reason = "completed"
            elif current.status != "in_progress" and task.status == "in_progress":
                reason = "claimed"
            else:
                reason = "updated"
            await self._emit_task_changes(snapshot, committed, reason=reason, primary_task_id=task_id)
            return {"ok": True, "task": _project_task(task)}
        except ValidationError:
            return {"ok": False, "error": {"code": "task_request_invalid"}}
        except TaskStateError as exc:
            return _task_error(exc)

    async def note(
        self,
        ctx: RunContext[AgentContext],
        key: str,
        value: str | None = None,
    ) -> NoteToolResult:
        from a13n_harness.capabilities.working_state import (
            _MAX_NOTE_KEY_LENGTH,
            _MAX_NOTE_VALUE_LENGTH,
            _WORKING_STATE_VERSION,
            WORKING_STATE_CAPABILITY_ID,
            _working_state_with,
        )

        self._require_context(ctx)
        if not key.strip() or "\x00" in key or len(key) > _MAX_NOTE_KEY_LENGTH:
            return {"ok": False, "error": {"code": "note_key_invalid"}}
        if value is not None and ("\x00" in value or len(value) > _MAX_NOTE_VALUE_LENGTH):
            return {"ok": False, "error": {"code": "note_value_invalid"}}
        async with self._state_lock:
            notes = self._state.notes
            if value is None:
                existed = notes.pop(key, None) is not None
            else:
                notes[key] = value
                existed = True
            state = _working_state_with(self._state, notes=notes)
            await ctx.deps.state.write(WORKING_STATE_CAPABILITY_ID, state, version=_WORKING_STATE_VERSION)
            self._state = state
        return {"ok": True, "key": key, "present": existed and value is not None}

    async def note_get(self, ctx: RunContext[AgentContext], key: str | None = None) -> NoteGetToolResult:
        self._require_context(ctx)
        notes = self._state.notes
        if key is None:
            return {"ok": True, "keys": sorted(notes)}
        if key not in notes:
            return {"ok": False, "error": {"code": "note_not_found", "key": key}}
        return {"ok": True, "key": key, "value": notes[key]}

    async def _task_result(self, operation: str, *args) -> TaskToolResult:
        from a13n_harness.capabilities.working_state import TaskStateError

        cell = self._require_cell()
        try:
            before = await cell.snapshot()
            if operation == "create":
                task = await cell.create(args[0])
            else:
                raise AssertionError(f"unsupported Working State operation: {operation}")
            committed = await cell.snapshot()
            await self._observe_provider(before)
            await self._emit_provider_changes(before)
            await self._observe_provider(committed)
            await self._emit_task_changes(before, committed, reason="created", primary_task_id=task.id)
            return {"ok": True, "task": _project_task(task)}
        except TaskStateError as exc:
            return _task_error(exc)

    async def _task_snapshot(self):
        state = await self._require_cell().snapshot()
        await self._observe_provider(state)
        await self._emit_provider_changes(state)
        return state

    async def _emit_provider_changes(self, state) -> None:
        if self._provider is None:
            return
        before = self._last_task_observation
        if before is None:
            from a13n_harness.capabilities.working_state import TaskState

            before = TaskState()
        await self._emit_task_changes(before, state, reason="provider_observed")

    def _require_context(self, ctx: RunContext[AgentContext]) -> None:
        from a13n_harness.capabilities.working_state import WORKING_STATE_CAPABILITY_ID

        if ctx.deps is not self._context or ctx.capabilities.get(WORKING_STATE_CAPABILITY_ID) is not self._owner:
            raise DefinitionError(
                "Working State Toolset cannot cross logical runs.",
                code="capability_scope_invalid",
            )

    async def ensure_bound(self, ctx: RunContext[AgentContext]) -> None:
        from a13n_harness.capabilities.working_state import (
            _WORKING_STATE_VERSION,
            TASK_STATE_RUN_CAPABILITY_ID,
            WORKING_STATE_CAPABILITY_ID,
            EmbeddedTaskStateCell,
            TaskState,
            TaskStateRunCapability,
            _working_state_with,
        )

        self._require_context(ctx)
        if self._task_binding_resolved:
            return
        attachment = ctx.capabilities.get(TASK_STATE_RUN_CAPABILITY_ID)
        if attachment is not None and type(attachment) is not TaskStateRunCapability:
            raise DefinitionError(
                "Task-state run attachment has an incompatible type.",
                code="task_state_type_mismatch",
            )
        if attachment is not None and TASK_STATE_RUN_CAPABILITY_ID not in self._context._capability_provenance.run_ids:
            raise DefinitionError(
                "Task-state attachment must originate from RunBindings.",
                code="capability_scope_invalid",
            )
        if not self._configuration.tasks_enabled:
            self._task_binding_resolved = True
            return

        if self._configuration.task_mode == "embedded":
            if type(attachment) is TaskStateRunCapability:
                if attachment.source != "embedded_borrowed":
                    raise DefinitionError(
                        "Embedded task mode requires an embedded_borrowed attachment.",
                        code="task_state_mode_mismatch",
                    )
                if self._context.instance.parent_agent_instance_id is None:
                    raise DefinitionError(
                        "An independent root cannot borrow a parent embedded task scope.",
                        code="task_state_borrow_forbidden",
                    )
                if self._state.tasks is not None and self._state.tasks.tasks:
                    raise DefinitionError(
                        "A borrowed child continuation must not contain a private task snapshot.",
                        code="task_state_snapshot_conflict",
                    )
                self._cell = attachment.cell
                self._state = _working_state_with(self._state, tasks=None)
            else:
                embedded_state = self._state.tasks or TaskState()
                self._cell = EmbeddedTaskStateCell(
                    embedded_state,
                    owner="root",
                    on_change=self._replace_embedded_tasks,
                )
            self._task_binding_resolved = True
            return

        if self._state.tasks is not None:
            raise DefinitionError(
                "Provider task mode cannot restore an embedded task map.",
                code="task_state_snapshot_conflict",
            )
        if type(attachment) is not TaskStateRunCapability or attachment.source != "provider":
            raise DefinitionError(
                "Provider task mode requires one fresh provider TaskStateRunCapability.",
                code="task_state_binding_missing",
            )
        self._cell = attachment.cell
        self._provider = attachment
        self._task_binding_resolved = True
        cursor = self._state.provider_cursor
        if cursor is not None and (
            cursor.provider_type != attachment.provider_type or cursor.state_version != attachment.state_version
        ):
            self._state = _working_state_with(self._state, provider_cursor=None)
            await self._context.state.write(
                WORKING_STATE_CAPABILITY_ID,
                self._state,
                version=_WORKING_STATE_VERSION,
            )

    def _require_cell(self):
        if self._cell is None:
            raise DefinitionError("Working State task cell is unavailable.", code="task_state_binding_missing")
        return self._cell

    async def _replace_embedded_tasks(self, tasks) -> None:
        from a13n_harness.capabilities.working_state import (
            _WORKING_STATE_VERSION,
            WORKING_STATE_CAPABILITY_ID,
            _working_state_with,
        )

        async with self._state_lock:
            state = _working_state_with(self._state, tasks=tasks)
            await self._context.state.write(
                WORKING_STATE_CAPABILITY_ID,
                state,
                version=_WORKING_STATE_VERSION,
            )
            self._state = state

    async def _emit_task_changes(self, before, after, *, reason, primary_task_id=None) -> None:
        changed = [task_id for task_id in sorted(after.tasks) if before.tasks.get(task_id) != after.tasks[task_id]]
        if not changed:
            return
        self._last_task_observation = after.model_copy(deep=True)
        reciprocal_task_ids: set[str] = set()
        if primary_task_id is not None:
            for primary in (before.tasks.get(primary_task_id), after.tasks.get(primary_task_id)):
                if primary is not None:
                    reciprocal_task_ids.update(primary.blocks)
                    reciprocal_task_ids.update(primary.blocked_by)
        operation_id = f"task-change-{token_urlsafe(9)}"
        for task_id in changed:
            task = after.tasks[task_id]
            if primary_task_id is None or task_id == primary_task_id:
                event_reason = reason
            elif self._provider is not None and task_id not in reciprocal_task_ids:
                event_reason = "provider_observed"
            else:
                event_reason = "dependency_updated"
            await emit_harness_event(
                self._context.events,
                kind="state",
                payload=TaskChangedPayload(
                    operation_id=operation_id,
                    state_revision=after.revision,
                    reason=event_reason,
                    task=TaskEventProjection(
                        id=task.id,
                        revision=task.revision,
                        subject=task.subject,
                        active_form=task.active_form,
                        status=task.status,
                        owner=task.owner,
                        blocks=task.blocks,
                        blocked_by=task.blocked_by,
                    ),
                ),
            )

    async def _observe_provider(self, snapshot=None) -> None:
        from a13n_harness.capabilities.working_state import (
            _WORKING_STATE_VERSION,
            WORKING_STATE_CAPABILITY_ID,
            ProviderTaskCursor,
            _working_state_with,
        )

        provider = self._provider
        if provider is None:
            return
        observed = snapshot or await provider.cell.snapshot()
        cursor = ProviderTaskCursor(
            provider_type=provider.provider_type,
            state_version=provider.state_version,
            observed_revision=observed.revision,
        )
        async with self._state_lock:
            current_cursor = self._state.provider_cursor
            if (
                current_cursor is not None
                and current_cursor.provider_type == cursor.provider_type
                and current_cursor.state_version == cursor.state_version
                and current_cursor.observed_revision is not None
                and current_cursor.observed_revision >= observed.revision
            ):
                return
            state = _working_state_with(self._state, tasks=None, provider_cursor=cursor)
            await self._context.state.write(
                WORKING_STATE_CAPABILITY_ID,
                state,
                version=_WORKING_STATE_VERSION,
            )
            self._state = state


def _project_task(task) -> TaskProjection:
    """Hide internal CAS revisions while retaining useful coordination state."""
    return {
        "id": task.id,
        "subject": task.subject,
        "description": task.description,
        "active_form": task.active_form,
        "status": task.status,
        "owner": task.owner,
        "blocks": list(task.blocks),
        "blocked_by": list(task.blocked_by),
        "metadata": task.metadata,
    }


def _task_error(exc) -> ToolFailure:
    details = deepcopy(exc.details)
    error: ToolError = {
        "code": exc.code,
        "retry_hint": exc.retry_hint,
        "details": details,
    }
    return {"ok": False, "error": error}


__all__ = [
    "NoteGetToolResult",
    "NoteToolResult",
    "TaskListToolResult",
    "TaskProjection",
    "TaskToolResult",
    "WorkingStateToolset",
]
