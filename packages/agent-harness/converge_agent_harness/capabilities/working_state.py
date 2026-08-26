"""Immutable task and note working state with linearizable task cells."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from html import escape
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AbstractToolset

from converge_agent_harness._json import dump_json_bytes
from converge_agent_harness.context import AgentContext
from converge_agent_harness.errors import DefinitionError, RunError
from converge_agent_harness.model_context import (
    AbstractModelContextCapability,
    ModelContextBlock,
    ModelContextNext,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextProjectionRequest,
)

WORKING_STATE_CAPABILITY_ID = "converge.working-state"
TASK_STATE_RUN_CAPABILITY_ID = "converge.working-state.tasks.run"
_WORKING_STATE_VERSION = "1"
_WORKING_STATE_OPEN = '<working-state source="converge-harness">'
_WORKING_STATE_CLOSE = "</working-state>"
_TASK_ID_PATTERN = re.compile(r"^task-([1-9][0-9]*)$")
_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])
_EMPTY_JSON_OBJECT = _JSON_OBJECT_ADAPTER.dump_json({})
_MAX_TASK_SUBJECT_LENGTH = 512
_MAX_TASK_DESCRIPTION_LENGTH = 16 * 1024
_MAX_TASK_ACTIVE_FORM_LENGTH = 512
_MAX_TASK_OWNER_LENGTH = 256
_MAX_TASK_DEPENDENCIES = 256
_MAX_NOTE_KEY_LENGTH = 512
_MAX_NOTE_VALUE_LENGTH = 64 * 1024
_MAX_NOTES = 10_000


class TaskStateError(RunError):
    """Typed task lookup, eligibility, dependency, or revision failure."""


class Task(BaseModel):
    """One immutable scope-local coordination task."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        revalidate_instances="always",
        validate_by_name=True,
    )

    id: str = Field(pattern=r"^task-[1-9][0-9]*$", max_length=64)
    revision: int = Field(ge=1)
    subject: str = Field(min_length=1, max_length=_MAX_TASK_SUBJECT_LENGTH)
    description: str = Field(min_length=1, max_length=_MAX_TASK_DESCRIPTION_LENGTH)
    active_form: str | None = Field(default=None, min_length=1, max_length=_MAX_TASK_ACTIVE_FORM_LENGTH)
    status: Literal["pending", "in_progress", "completed"] = "pending"
    owner: str | None = Field(default=None, min_length=1, max_length=_MAX_TASK_OWNER_LENGTH)
    blocks: tuple[str, ...] = Field(default=(), max_length=_MAX_TASK_DEPENDENCIES)
    blocked_by: tuple[str, ...] = Field(default=(), max_length=_MAX_TASK_DEPENDENCIES)
    metadata_json: bytes | Mapping[str, JsonValue] = Field(
        default=_EMPTY_JSON_OBJECT,
        alias="metadata",
        exclude=True,
        repr=False,
    )

    @field_validator("metadata_json", mode="before")
    @classmethod
    def _encode_metadata(cls, value: Any) -> bytes:
        normalized = (
            _JSON_OBJECT_ADAPTER.validate_json(value)
            if isinstance(value, bytes)
            else _JSON_OBJECT_ADAPTER.validate_python(value)
        )
        return dump_json_bytes(normalized, sort_keys=True)

    @field_validator("blocks", "blocked_by")
    @classmethod
    def _validate_dependencies(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value)
        if len(set(normalized)) != len(normalized) or any(
            len(item) > 64 or _TASK_ID_PATTERN.fullmatch(item) is None for item in normalized
        ):
            raise ValueError("task dependencies must contain unique canonical task references")
        return normalized

    @model_validator(mode="after")
    def _reject_self_dependency(self) -> Task:
        if self.id in self.blocks or self.id in self.blocked_by:
            raise ValueError("a task cannot depend on itself")
        return self

    @computed_field
    @property
    def metadata(self) -> dict[str, JsonValue]:
        """Return detached metadata so mutation cannot bypass a cell revision."""
        if not isinstance(self.metadata_json, bytes):
            raise AssertionError("Task metadata must be normalized to bytes")
        return _JSON_OBJECT_ADAPTER.validate_json(self.metadata_json)


_TASKS_ADAPTER = TypeAdapter(dict[str, Task])
_EMPTY_TASKS_JSON = _TASKS_ADAPTER.dump_json({})


class TaskState(BaseModel):
    """Immutable replacement snapshot for one task scope."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always", validate_by_name=True)

    revision: int = Field(default=0, ge=0)
    next_task_sequence: int = Field(default=1, ge=1)
    tasks_json: bytes | Mapping[str, Task] = Field(
        default=_EMPTY_TASKS_JSON,
        alias="tasks",
        exclude=True,
        repr=False,
    )

    @field_validator("tasks_json", mode="before")
    @classmethod
    def _encode_tasks(cls, value: Any) -> bytes:
        tasks = (
            _TASKS_ADAPTER.validate_json(value) if isinstance(value, bytes) else _TASKS_ADAPTER.validate_python(value)
        )
        for task_id, task in tasks.items():
            if task_id != task.id:
                raise ValueError("task map keys must match Task.id")
        return _TASKS_ADAPTER.dump_json(tasks)

    @model_validator(mode="after")
    def _validate_allocator_and_revisions(self) -> TaskState:
        tasks = self.tasks
        sequences = [_task_sequence(task_id) for task_id in tasks]
        if sequences and self.next_task_sequence <= max(sequences):
            raise ValueError("next_task_sequence must remain above every allocated task reference")
        if any(task.revision > self.revision for task in tasks.values()):
            raise ValueError("task revisions cannot exceed the task-state revision")
        _validate_dependency_graph(tasks)
        return self

    @computed_field
    @property
    def tasks(self) -> dict[str, Task]:
        """Return a detached task map; callers cannot mutate the stored snapshot."""
        if not isinstance(self.tasks_json, bytes):
            raise AssertionError("Task state must be normalized to bytes")
        return _TASKS_ADAPTER.validate_json(self.tasks_json)


class ProviderTaskCursor(BaseModel):
    """Non-authoritative provider compatibility and observation metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    provider_type: str = Field(min_length=1, max_length=256)
    state_version: str = Field(min_length=1, max_length=256)
    observed_revision: int | None = Field(default=None, ge=0)


class WorkingState(BaseModel):
    """One immutable Capability-owned replacement containing tasks and private notes."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always", validate_by_name=True)

    task_mode: Literal["embedded", "provider"] = "embedded"
    tasks: TaskState | None = None
    provider_cursor: ProviderTaskCursor | None = None
    notes_json: bytes | Mapping[str, str] = Field(
        default=b"{}",
        alias="notes",
        exclude=True,
        repr=False,
    )

    @field_validator("notes_json", mode="before")
    @classmethod
    def _encode_notes(cls, value: Any) -> bytes:
        adapter = TypeAdapter(dict[str, str])
        notes = adapter.validate_json(value) if isinstance(value, bytes) else adapter.validate_python(value)
        if len(notes) > _MAX_NOTES:
            raise ValueError("working state contains too many notes")
        if any(not key.strip() or "\x00" in key or len(key) > _MAX_NOTE_KEY_LENGTH for key in notes):
            raise ValueError("note keys must be bounded, non-blank, and must not contain NUL")
        if any("\x00" in note or len(note) > _MAX_NOTE_VALUE_LENGTH for note in notes.values()):
            raise ValueError("note values must be bounded and must not contain NUL")
        return adapter.dump_json(notes)

    @model_validator(mode="after")
    def _validate_task_mode(self) -> WorkingState:
        if self.task_mode == "provider" and self.tasks is not None:
            raise ValueError("provider task mode cannot contain an embedded task snapshot")
        if self.task_mode == "embedded" and self.provider_cursor is not None:
            raise ValueError("embedded task mode cannot contain a provider cursor")
        return self

    @computed_field
    @property
    def notes(self) -> dict[str, str]:
        """Return detached note values."""
        if not isinstance(self.notes_json, bytes):
            raise AssertionError("Working-state notes must be normalized to bytes")
        return TypeAdapter(dict[str, str]).validate_json(self.notes_json)


class CreateTask(BaseModel):
    """Validated request for atomic task allocation and dependency insertion."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True, revalidate_instances="always")

    subject: str = Field(min_length=1, max_length=_MAX_TASK_SUBJECT_LENGTH)
    description: str = Field(min_length=1, max_length=_MAX_TASK_DESCRIPTION_LENGTH)
    active_form: str | None = Field(default=None, min_length=1, max_length=_MAX_TASK_ACTIVE_FORM_LENGTH)
    blocked_by: tuple[str, ...] = Field(default=(), max_length=_MAX_TASK_DEPENDENCIES)
    blocks: tuple[str, ...] = Field(default=(), max_length=_MAX_TASK_DEPENDENCIES)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class TaskMutation(BaseModel):
    """One compare-and-swap task mutation without model-authored ownership."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True, revalidate_instances="always")

    subject: str | None = Field(default=None, min_length=1, max_length=_MAX_TASK_SUBJECT_LENGTH)
    description: str | None = Field(default=None, min_length=1, max_length=_MAX_TASK_DESCRIPTION_LENGTH)
    active_form: str | None = Field(default=None, min_length=1, max_length=_MAX_TASK_ACTIVE_FORM_LENGTH)
    clear_active_form: bool = False
    status: Literal["pending", "in_progress", "completed"] | None = None
    add_blocks: tuple[str, ...] = Field(default=(), max_length=_MAX_TASK_DEPENDENCIES)
    remove_blocks: tuple[str, ...] = Field(default=(), max_length=_MAX_TASK_DEPENDENCIES)
    add_blocked_by: tuple[str, ...] = Field(default=(), max_length=_MAX_TASK_DEPENDENCIES)
    remove_blocked_by: tuple[str, ...] = Field(default=(), max_length=_MAX_TASK_DEPENDENCIES)
    metadata: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def _validate_clear_operations(self) -> TaskMutation:
        if self.clear_active_form and self.active_form is not None:
            raise ValueError("active_form and clear_active_form are mutually exclusive")
        return self


@runtime_checkable
class TaskStateCell(Protocol):
    """Linearizable task view already bound to one trusted Agent instance."""

    async def snapshot(self) -> TaskState: ...

    async def create(self, request: CreateTask) -> Task: ...

    async def mutate(
        self,
        task_id: str,
        mutation: TaskMutation,
        expected_revision: int | None,
        *,
        claim: bool = False,
    ) -> Task: ...

    async def claim(self, task_id: str, expected_revision: int | None = None) -> Task: ...

    async def update(self, task_id: str, mutation: TaskMutation, expected_revision: int) -> Task: ...


TaskStateChange = Callable[[TaskState], Awaitable[None]]


@dataclass(slots=True)
class _EmbeddedTaskBackend:
    state: TaskState
    on_change: TaskStateChange | None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class EmbeddedTaskStateCell:
    """Reference embedded linearizable cell with bindable safe owner labels."""

    def __init__(
        self,
        state: TaskState | None = None,
        *,
        owner: str = "root",
        on_change: TaskStateChange | None = None,
        _backend: _EmbeddedTaskBackend | None = None,
    ) -> None:
        if not isinstance(owner, str) or not owner.strip() or len(owner) > _MAX_TASK_OWNER_LENGTH:
            raise ValueError("task cell owner must be bounded and non-blank")
        self._owner = owner
        self._backend = _backend or _EmbeddedTaskBackend(
            state=(state or TaskState()).model_copy(deep=True),
            on_change=on_change,
        )

    def bind(self, owner: str) -> EmbeddedTaskStateCell:
        """Return another identity-bound view over the same embedded scope and allocator."""
        return EmbeddedTaskStateCell(owner=owner, _backend=self._backend)

    async def snapshot(self) -> TaskState:
        async with self._backend.lock:
            return self._backend.state.model_copy(deep=True)

    async def create(self, request: CreateTask) -> Task:
        try:
            request = _validate_model(request, CreateTask)
        except ValidationError as exc:
            raise TaskStateError("Task creation input is invalid.", code="task_request_invalid") from exc
        async with self._backend.lock:
            current = self._backend.state
            tasks = current.tasks
            _require_references(tasks, (*request.blocks, *request.blocked_by))
            task_id = f"task-{current.next_task_sequence}"
            revision = current.revision + 1
            created = Task(
                id=task_id,
                revision=revision,
                subject=request.subject,
                description=request.description,
                active_form=request.active_form,
                blocks=tuple(dict.fromkeys(request.blocks)),
                blocked_by=tuple(dict.fromkeys(request.blocked_by)),
                metadata=request.metadata,
            )
            tasks[task_id] = created
            for other_id in created.blocks:
                tasks[other_id] = _task_with(
                    tasks[other_id],
                    revision=revision,
                    blocked_by=_add_ref(tasks[other_id].blocked_by, task_id),
                )
            for other_id in created.blocked_by:
                tasks[other_id] = _task_with(
                    tasks[other_id],
                    revision=revision,
                    blocks=_add_ref(tasks[other_id].blocks, task_id),
                )
            _require_valid_dependency_graph(tasks)
            state = TaskState(
                revision=revision,
                next_task_sequence=current.next_task_sequence + 1,
                tasks=tasks,
            )
            await self._commit(state)
            return state.tasks[task_id]

    async def mutate(
        self,
        task_id: str,
        mutation: TaskMutation,
        expected_revision: int | None,
        *,
        claim: bool = False,
    ) -> Task:
        """Apply an optional implicit claim and mutation in one persisted revision."""
        _require_task_id(task_id)
        try:
            mutation = _validate_model(mutation, TaskMutation)
        except ValidationError as exc:
            raise TaskStateError("Task mutation input is invalid.", code="task_request_invalid") from exc
        async with self._backend.lock:
            current = self._backend.state
            tasks = current.tasks
            task = _require_task(tasks, task_id)
            if claim and _mutation_is_empty(mutation) and task.owner == self._owner and task.status == "in_progress":
                return task
            if expected_revision is not None and task.revision != expected_revision:
                raise TaskStateError(
                    "Task revision does not match the expected revision.",
                    code="task_revision_conflict",
                    details={"task_id": task_id, "expected": expected_revision, "actual": task.revision},
                )
            if task.owner is not None and task.owner != self._owner:
                raise TaskStateError(
                    "Task is already claimed by another owner.",
                    code="task_owner_conflict",
                    details={"task_id": task_id, "owner": task.owner},
                )
            if not claim and mutation.status in {"in_progress", "completed"} and task.owner != self._owner:
                raise TaskStateError(
                    "Task status changes require ownership.",
                    code="task_owner_conflict",
                    details={"task_id": task_id, "owner": task.owner},
                )

            staged = task
            if claim and not (task.owner == self._owner and task.status == "in_progress"):
                if task.status == "completed":
                    raise TaskStateError("Completed tasks cannot be claimed.", code="task_not_claimable")
                blockers = [dependency for dependency in task.blocked_by if tasks[dependency].status != "completed"]
                if blockers:
                    raise TaskStateError(
                        "Task dependencies are not complete.",
                        code="task_blocked",
                        details=_JSON_OBJECT_ADAPTER.validate_python({"task_id": task_id, "blocked_by": blockers}),
                    )
                staged = _task_with(task, status="in_progress", owner=self._owner)

            dependency_refs = (
                *mutation.add_blocks,
                *mutation.remove_blocks,
                *mutation.add_blocked_by,
                *mutation.remove_blocked_by,
            )
            _require_references(tasks, dependency_refs)
            if task_id in dependency_refs:
                raise TaskStateError("A task cannot depend on itself.", code="task_dependency_invalid")

            metadata = staged.metadata
            if mutation.metadata is not None:
                metadata.update(deepcopy(mutation.metadata))
            updates: dict[str, Any] = {
                "blocks": _mutate_refs(staged.blocks, mutation.add_blocks, mutation.remove_blocks),
                "blocked_by": _mutate_refs(
                    staged.blocked_by,
                    mutation.add_blocked_by,
                    mutation.remove_blocked_by,
                ),
                "metadata": metadata,
            }
            for name in ("subject", "description", "status"):
                value = getattr(mutation, name)
                if value is not None:
                    updates[name] = value
            if mutation.clear_active_form:
                updates["active_form"] = None
            elif mutation.active_form is not None:
                updates["active_form"] = mutation.active_form

            staged = _task_with(staged, **updates)
            if staged == task:
                return task
            revision = current.revision + 1
            tasks[task_id] = _task_with(staged, revision=revision)
            _update_reciprocal_dependencies(
                tasks,
                task_id=task_id,
                previous=task,
                current=tasks[task_id],
                revision=revision,
            )
            _require_valid_dependency_graph(tasks)
            state = TaskState(
                revision=revision,
                next_task_sequence=current.next_task_sequence,
                tasks=tasks,
            )
            await self._commit(state)
            return state.tasks[task_id]

    async def claim(self, task_id: str, expected_revision: int | None = None) -> Task:
        return await self.mutate(task_id, TaskMutation(), expected_revision, claim=True)

    async def update(self, task_id: str, mutation: TaskMutation, expected_revision: int) -> Task:
        return await self.mutate(task_id, mutation, expected_revision)

    async def _commit(self, state: TaskState) -> None:
        if self._backend.on_change is not None:
            await self._backend.on_change(state)
        self._backend.state = state


@dataclass(kw_only=True)
class TaskStateRunCapability(AbstractCapability[AgentContext]):
    """Fresh Host attachment carrying one already identity-bound task cell."""

    id: str | None = TASK_STATE_RUN_CAPABILITY_ID
    source: Literal["embedded_borrowed", "provider"]
    cell: TaskStateCell
    provider_type: str = "embedded"
    state_version: str = "1"

    def __post_init__(self) -> None:
        if self.id != TASK_STATE_RUN_CAPABILITY_ID:
            raise ValueError(f"TaskStateRunCapability.id must be {TASK_STATE_RUN_CAPABILITY_ID!r}")
        if not isinstance(self.cell, TaskStateCell):
            raise TypeError("TaskStateRunCapability.cell must implement TaskStateCell")
        if not self.provider_type.strip() or not self.state_version.strip():
            raise ValueError("task provider metadata must be non-blank")


class WorkingStateConfiguration(BaseModel):
    """Definition-selected working-state mode and model surface."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    task_mode: Literal["embedded", "provider"] = "embedded"
    tasks_enabled: bool = True
    notes_enabled: bool = True
    max_context_tasks: int = Field(default=128, gt=0, le=10_000)
    max_context_note_keys: int = Field(default=256, gt=0, le=10_000)
    max_context_bytes: int = Field(default=64 * 1024, ge=1024, le=256 * 1024)


@dataclass(init=False)
class WorkingStateCapability(AbstractModelContextCapability):
    """Own one task-and-note namespace while preserving distinct sharing rules."""

    id = WORKING_STATE_CAPABILITY_ID

    def __init__(self, configuration: WorkingStateConfiguration | None = None) -> None:
        self.configuration = (configuration or WorkingStateConfiguration()).model_copy(deep=True)

    async def bind_inline_child_task_state(
        self,
        ctx: RunContext[AgentContext],
        *,
        owner: str,
    ) -> TaskStateRunCapability | None:
        """Borrow the exact active embedded task store for one trusted child identity."""
        del ctx, owner
        raise DefinitionError(
            "Working State task borrowing requires the active run replacement.",
            code="capability_scope_invalid",
        )

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(WORKING_STATE_CAPABILITY_ID)
        if existing is not None:
            if not isinstance(existing, _WorkingStateRunCapability):
                raise DefinitionError(
                    "Working State has an incompatible run replacement.",
                    code="capability_type_mismatch",
                )
            return existing
        if WORKING_STATE_CAPABILITY_ID not in ctx.deps._capability_provenance.definition_ids:
            raise DefinitionError(
                "WorkingStateCapability must originate from the Agent definition.",
                code="capability_scope_invalid",
            )
        state = await ctx.deps.state.read(
            WORKING_STATE_CAPABILITY_ID,
            WorkingState,
            version=_WORKING_STATE_VERSION,
        )
        if state is None:
            state = WorkingState(
                task_mode=self.configuration.task_mode,
                tasks=TaskState() if self.configuration.task_mode == "embedded" else None,
            )
        if state.task_mode != self.configuration.task_mode:
            raise DefinitionError(
                "Restored Working State task mode does not match the Agent definition.",
                code="working_state_mode_mismatch",
            )

        replacement = _WorkingStateRunCapability(
            self.configuration,
            context=ctx.deps,
            state=state,
        )
        ctx.deps._record_run_capability(WORKING_STATE_CAPABILITY_ID, replacement)
        return replacement


@dataclass(init=False)
class _WorkingStateRunCapability(WorkingStateCapability):
    def __init__(
        self,
        configuration: WorkingStateConfiguration,
        *,
        context: AgentContext,
        state: WorkingState,
    ) -> None:
        super().__init__(configuration)
        from converge_agent_harness.toolsets.working_state import WorkingStateToolset

        self._context = context
        self._toolset = WorkingStateToolset(
            owner=self,
            context=context,
            state=state,
            configuration=self.configuration,
        )

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        if ctx.deps is not self._context:
            raise DefinitionError(
                "Working State run replacement cannot cross logical runs.", code="capability_scope_invalid"
            )
        return self

    async def bind_inline_child_task_state(
        self,
        ctx: RunContext[AgentContext],
        *,
        owner: str,
    ) -> TaskStateRunCapability | None:
        cell = await self._toolset.embedded_task_cell(ctx)
        if cell is None:
            return None
        return TaskStateRunCapability(source="embedded_borrowed", cell=cell.bind(owner))

    def get_toolset(self) -> AbstractToolset[AgentContext] | None:
        return self._toolset.get_toolset(
            tasks=self.configuration.tasks_enabled,
            notes=self.configuration.notes_enabled,
        )

    def get_instructions(self) -> str:
        return (
            "Use tasks only when a visible checklist materially improves multi-step execution or coordination. "
            "Task references are scope-local labels, not authority. Set a task to in_progress when starting it and "
            "completed immediately after finishing it; concurrency and ownership checks remain internal. Use notes "
            "for concise private facts worth retaining; note values are loaded only on demand."
        )

    async def wrap_model_context(
        self,
        ctx: RunContext[AgentContext],
        request: ModelContextProjectionRequest,
        handler: ModelContextNext,
    ) -> ModelContextProjection:
        await self._toolset.ensure_bound(ctx)
        projection = await handler(request)
        snapshot = await self._toolset.context_snapshot(ctx)
        all_tasks = snapshot.tasks if snapshot is not None else {}
        tasks = sorted(
            (task for task in all_tasks.values() if task.status != "completed"),
            key=lambda item: _task_sequence(item.id),
        )
        note_keys = sorted(self._toolset.note_keys()) if self.configuration.notes_enabled else []
        rendered = _render_working_state(tasks, all_tasks, note_keys, self.configuration)
        return ModelContextProjection(
            blocks=(
                *projection.blocks,
                ModelContextBlock(
                    source_id=WORKING_STATE_CAPABILITY_ID,
                    placement=ModelContextPlacement.REQUEST_EPILOGUE,
                    content=rendered,
                ),
            )
        )


def _render_working_state(
    tasks: Sequence[Task],
    all_tasks: Mapping[str, Task],
    note_keys: Sequence[str],
    configuration: WorkingStateConfiguration,
) -> str:
    """Render a stable prefix without ever exceeding the configured UTF-8 budget."""
    lines = [_WORKING_STATE_OPEN]
    budget = configuration.max_context_bytes

    def append(line: str, *, reserve: int = 0) -> bool:
        candidate = "\n".join((*lines, line, _WORKING_STATE_CLOSE)).encode("utf-8")
        if len(candidate) + reserve > budget:
            return False
        lines.append(line)
        return True

    shown_tasks = 0
    for task in tasks[: configuration.max_context_tasks]:
        owner = f' owner="{escape(task.owner, quote=True)}"' if task.owner is not None else ""
        active_blockers = tuple(task_id for task_id in task.blocked_by if all_tasks[task_id].status != "completed")
        blocked = f' blocked-by="{escape(",".join(active_blockers), quote=True)}"' if active_blockers else ""
        line = f'  <task id="{task.id}" status="{task.status}"{owner}{blocked}>{escape(task.subject)}</task>'
        if not append(line, reserve=256):
            break
        shown_tasks += 1
    omitted_tasks = len(tasks) - shown_tasks
    if omitted_tasks:
        append(f'  <tasks-omitted count="{omitted_tasks}" />', reserve=128)

    shown_notes = 0
    for key in note_keys[: configuration.max_context_note_keys]:
        if not append(f"  <note-key>{escape(key)}</note-key>", reserve=128):
            break
        shown_notes += 1
    omitted_notes = len(note_keys) - shown_notes
    if omitted_notes:
        append(f'  <note-keys-omitted count="{omitted_notes}" />')

    lines.append(_WORKING_STATE_CLOSE)
    rendered = "\n".join(lines)
    if len(rendered.encode("utf-8")) > budget:
        raise AssertionError("working-state context budget invariant violated")
    return rendered


def _mutation_is_empty(mutation: TaskMutation) -> bool:
    return (
        mutation.subject is None
        and mutation.description is None
        and mutation.active_form is None
        and not mutation.clear_active_form
        and mutation.status is None
        and not mutation.add_blocks
        and not mutation.remove_blocks
        and not mutation.add_blocked_by
        and not mutation.remove_blocked_by
        and mutation.metadata is None
    )


def _working_state_with(state: WorkingState, **updates: Any) -> WorkingState:
    data: dict[str, Any] = {
        "task_mode": state.task_mode,
        "tasks": state.tasks,
        "provider_cursor": state.provider_cursor,
        "notes": state.notes,
    }
    data.update(updates)
    return WorkingState.model_validate(data)


def _task_with(task: Task, **updates: Any) -> Task:
    data = task.model_dump(mode="python")
    data.update(updates)
    return Task.model_validate(data)


def _task_sequence(task_id: str) -> int:
    match = _TASK_ID_PATTERN.fullmatch(task_id)
    if match is None:
        raise ValueError(f"invalid task reference: {task_id!r}")
    return int(match.group(1))


def _require_task_id(task_id: str) -> None:
    if not isinstance(task_id, str) or _TASK_ID_PATTERN.fullmatch(task_id) is None:
        raise TaskStateError("Task reference is not canonical.", code="task_reference_invalid")


def _require_task(tasks: Mapping[str, Task], task_id: str) -> Task:
    _require_task_id(task_id)
    try:
        return tasks[task_id]
    except KeyError:
        raise TaskStateError(
            "Task does not exist in this task scope.",
            code="task_not_found",
            details={"task_id": task_id},
        ) from None


def _require_references(tasks: Mapping[str, Task], references: Sequence[str]) -> None:
    for task_id in references:
        _require_task(tasks, task_id)


def _add_ref(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    return values if value in values else (*values, value)


def _remove_ref(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    return tuple(item for item in values if item != value)


def _mutate_refs(current: tuple[str, ...], additions: Sequence[str], removals: Sequence[str]) -> tuple[str, ...]:
    values = list(current)
    for value in additions:
        if value not in values:
            values.append(value)
    removed = set(removals)
    return tuple(value for value in values if value not in removed)


def _update_reciprocal_dependencies(
    tasks: dict[str, Task],
    *,
    task_id: str,
    previous: Task,
    current: Task,
    revision: int,
) -> None:
    for other_id in set(previous.blocks) | set(current.blocks):
        other = tasks[other_id]
        blocked_by = (
            _add_ref(other.blocked_by, task_id)
            if other_id in current.blocks
            else _remove_ref(other.blocked_by, task_id)
        )
        if blocked_by != other.blocked_by:
            tasks[other_id] = _task_with(other, revision=revision, blocked_by=blocked_by)
    for other_id in set(previous.blocked_by) | set(current.blocked_by):
        other = tasks[other_id]
        blocks = (
            _add_ref(other.blocks, task_id) if other_id in current.blocked_by else _remove_ref(other.blocks, task_id)
        )
        if blocks != other.blocks:
            tasks[other_id] = _task_with(other, revision=revision, blocks=blocks)


def _require_valid_dependency_graph(tasks: Mapping[str, Task]) -> None:
    try:
        _validate_dependency_graph(tasks)
    except ValueError as exc:
        raise TaskStateError(
            "Task dependency mutation is invalid.",
            code="task_dependency_invalid",
        ) from exc


def _validate_dependency_graph(tasks: Mapping[str, Task]) -> None:
    for task in tasks.values():
        for blocked_id in task.blocks:
            if blocked_id not in tasks or task.id not in tasks[blocked_id].blocked_by:
                raise ValueError("task dependency relationships must be reciprocal")
        for blocker_id in task.blocked_by:
            if blocker_id not in tasks or task.id not in tasks[blocker_id].blocks:
                raise ValueError("task dependency relationships must be reciprocal")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise ValueError("task dependency graph must be acyclic")
        if task_id in visited:
            return
        visiting.add(task_id)
        for blocker in tasks[task_id].blocked_by:
            visit(blocker)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in tasks:
        visit(task_id)


def _validate_model[T: BaseModel](value: T, model_type: type[T]) -> T:
    return (
        value.model_copy(deep=True) if isinstance(value, model_type) else model_type.model_validate(value, strict=True)
    )


__all__ = [
    "CreateTask",
    "EmbeddedTaskStateCell",
    "ProviderTaskCursor",
    "Task",
    "TaskMutation",
    "TaskState",
    "TaskStateCell",
    "TaskStateError",
    "TaskStateRunCapability",
    "WorkingState",
    "WorkingStateCapability",
    "WorkingStateConfiguration",
]
