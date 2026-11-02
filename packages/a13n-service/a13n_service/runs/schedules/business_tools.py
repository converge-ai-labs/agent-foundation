"""Explicit Schedule task contracts for internal Agents."""

from typing import Annotated, Literal

from pydantic import Field, JsonValue

from a13n_service.business_tools import ToolContext as BusinessContext
from a13n_service.business_tools import definition
from a13n_service.infra.ids import ObjectId
from a13n_service.infra.labels import Labels
from a13n_service.resources.memories.schemas import MemoryMounts
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schedules import dispatch, queries, service, tasks
from a13n_service.runs.schedules.schemas import (
    Occurrence,
    OccurrencePage,
    OccurrenceQuery,
    Schedule,
    ScheduleCreate,
    SchedulePage,
    SchedulePreview,
    SchedulePreviewRequest,
    ScheduleQuery,
    ScheduleRule,
    ScheduleStatus,
    ScheduleTarget,
)
from a13n_service.runs.schemas import context_response

type ToolContext = BusinessContext[Runtime]


async def create_schedule(
    ctx: ToolContext,
    *,
    name: Annotated[str, Field(min_length=1, max_length=128)],
    instructions: Annotated[str, Field(min_length=1, max_length=65536)],
    agent_id: ObjectId,
    schedule: ScheduleRule,
    idempotency_key: Annotated[str, Field(min_length=1, max_length=256)],
    agent_revision_id: ObjectId | None = None,
    description: Annotated[str | None, Field(max_length=2048)] = None,
    structured_inputs: Annotated[tuple[JsonValue, ...], Field(max_length=31)] = (),
    options: tasks.TaskOptions | None = None,
    memories: MemoryMounts = (),
    target: ScheduleTarget | None = None,
    labels: Labels | None = None,
    max_lateness_seconds: Annotated[int, Field(ge=60, le=86400)] = 300,
    state: Literal["paused", "enabled"] = "paused",
) -> Schedule:
    """Schedule a task. State enabled delegates future execution as the creator; default paused only saves it.

    The service builds and validates the invocation. Reuse an idempotency key only for identical intent.
    """
    body = ScheduleCreate(
        name=name,
        description=description,
        labels=labels or {},
        schedule=schedule,
        max_lateness_seconds=max_lateness_seconds,
        target=target or ScheduleTarget(),
        state=state,
        invocation=tasks.invocation(
            agent_id, agent_revision_id, instructions, structured_inputs, options or tasks.TaskOptions(), memories
        ),
    )
    result, _created = await service.create(ctx.runtime, ctx.actor, ctx.workspace_id, body, request_key=idempotency_key)
    return context_response(result)


async def update_schedule(
    ctx: ToolContext, *, schedule_id: ObjectId, expected_version: str, body: tasks.TaskChanges
) -> Schedule:
    """Change only specified task fields at the observed version. Omitted invocation and execution choices survive."""
    return context_response(
        await tasks.update_task(
            ctx.runtime, ctx.actor, ctx.workspace_id, schedule_id, body, expected_version=expected_version
        )
    )


async def list_schedules(ctx: ToolContext, *, filters: ScheduleQuery) -> SchedulePage:
    """List schedules using state, executor, creator, label and text filters."""
    return context_response(await queries.list_schedules(ctx.runtime.storage, ctx.actor, ctx.workspace_id, filters))


async def get_schedule(ctx: ToolContext, *, schedule_id: ObjectId) -> Schedule:
    """Read a schedule and its version before changing its task or lifecycle."""
    return context_response(await queries.get(ctx.runtime.storage, ctx.actor, ctx.workspace_id, schedule_id))


async def get_schedule_status(ctx: ToolContext, *, schedule_id: ObjectId) -> ScheduleStatus:
    """Observe next fire, pending work and unfinished occurrences."""
    return await queries.status(ctx.runtime.storage, ctx.actor, ctx.workspace_id, schedule_id)


async def preview_schedule(ctx: ToolContext, *, body: SchedulePreviewRequest) -> SchedulePreview:
    """Preview future firing times without creating or changing a task."""
    return await service.preview(ctx.runtime, ctx.actor, ctx.workspace_id, body)


async def enable_schedule(ctx: ToolContext, *, schedule_id: ObjectId, expected_version: str) -> Schedule:
    """Enable future execution of the saved task as its creator."""
    return context_response(
        await service.transition(
            ctx.runtime, ctx.actor, ctx.workspace_id, schedule_id, "enable", if_match=expected_version
        )
    )


async def pause_schedule(ctx: ToolContext, *, schedule_id: ObjectId, expected_version: str) -> Schedule:
    """Pause future scheduled execution without rewriting accepted occurrence snapshots."""
    return context_response(
        await service.transition(
            ctx.runtime, ctx.actor, ctx.workspace_id, schedule_id, "pause", if_match=expected_version
        )
    )


async def archive_schedule(ctx: ToolContext, *, schedule_id: ObjectId, expected_version: str) -> Schedule:
    """Archive a schedule at its observed version and return its actual lifecycle state."""
    return context_response(
        await service.transition(
            ctx.runtime, ctx.actor, ctx.workspace_id, schedule_id, "archive", if_match=expected_version
        )
    )


async def dispatch_schedule(
    ctx: ToolContext,
    *,
    schedule_id: ObjectId,
    expected_version: str,
    idempotency_key: Annotated[str, Field(min_length=1, max_length=256)],
) -> Occurrence:
    """Accept one manual occurrence, preserving immutable task snapshot and durable replay semantics."""
    result, _created = await dispatch.manual(
        ctx.runtime, ctx.actor, ctx.workspace_id, schedule_id, request_key=idempotency_key, if_match=expected_version
    )
    return context_response(result)


async def list_schedule_occurrences(
    ctx: ToolContext, *, schedule_id: ObjectId, filters: OccurrenceQuery
) -> OccurrencePage:
    """Read occurrence history with status, trigger, time, unfinished and execution-observation filters."""
    return await queries.list_occurrences(ctx.runtime.storage, ctx.actor, ctx.workspace_id, schedule_id, filters)


async def get_schedule_occurrence(
    ctx: ToolContext, *, schedule_id: ObjectId, occurrence_id: ObjectId, include: Literal["execution"] | None = None
) -> Occurrence:
    """Read one immutable occurrence snapshot with optional current execution observations."""
    return context_response(
        await queries.get_occurrence(
            ctx.runtime.storage,
            ctx.actor,
            ctx.workspace_id,
            schedule_id,
            occurrence_id,
            execution=include == "execution",
        )
    )


TOOLS = (
    definition(create_schedule, group="schedules", effect="write"),
    definition(update_schedule, group="schedules", effect="write"),
    definition(list_schedules, group="schedules", effect="read"),
    definition(get_schedule, group="schedules", effect="read"),
    definition(get_schedule_status, group="schedules", effect="read"),
    definition(preview_schedule, group="schedules", effect="read"),
    definition(enable_schedule, group="schedules", effect="write"),
    definition(pause_schedule, group="schedules", effect="write"),
    definition(archive_schedule, group="schedules", effect="write"),
    definition(dispatch_schedule, group="schedules", effect="write"),
    definition(list_schedule_occurrences, group="schedules", effect="read"),
    definition(get_schedule_occurrence, group="schedules", effect="read"),
)
