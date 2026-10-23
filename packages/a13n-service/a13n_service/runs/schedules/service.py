"""Authorized, versioned Schedule configuration and durable recurring delegation."""

import hashlib
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import advisory_lock, assign, now, short_session, transaction
from a13n_service.infra.errors import IDEMPOTENCY_KEY_REUSED, ServiceError, conflict, invalid, rate_limited
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.agents.validation import validate_config
from a13n_service.resources.memories.schemas import SessionMemorySettings
from a13n_service.resources.memories.service import resolve_memory
from a13n_service.resources.rows import audit_row
from a13n_service.runs.accept import run_revision
from a13n_service.runs.inbox import payload_size
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schedules import lifecycle, queries, webhooks
from a13n_service.runs.schedules.calendar import Calendar
from a13n_service.runs.schedules.schemas import (
    OnceSchedule,
    Schedule,
    ScheduleConfig,
    ScheduleCreate,
    ScheduleInvocation,
    SchedulePreview,
    SchedulePreviewRequest,
    ScheduleRule,
    ScheduleTime,
    ScheduleUpdate,
)
from a13n_service.runs.schedules.tables import ScheduleRow
from a13n_service.runs.schemas import canonical_json
from a13n_service.runs.sessions import new_session
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope, authorize, execution_authority


def request_digest(command: str, target: str, body: object) -> str:
    return hashlib.sha256(canonical_json([command, target, body])).hexdigest()


def require_owner(row: ScheduleRow, actor: Principal) -> None:
    if row.created_by_id != actor.id:
        raise ServiceError(
            "forbidden", "Only the Schedule creator may start or change its work", {"reason": "schedule_owner_required"}
        )


def require_editable(row: ScheduleRow) -> None:
    if row.state in {"archived", "completed"}:
        raise conflict("schedule", row.id, row.state)


def time_values(rule: ScheduleRule) -> dict[str, object]:
    if isinstance(rule, OnceSchedule):
        return {"schedule_type": "once", "run_at": rule.run_at, "cron_expression": None, "timezone": None}
    return {
        "schedule_type": "cron",
        "run_at": None,
        "cron_expression": Calendar.parse(rule.expression, rule.timezone).expression,
        "timezone": rule.timezone,
    }


def first_fire(rule: ScheduleRule, after: datetime) -> datetime:
    if isinstance(rule, OnceSchedule):
        if rule.run_at <= after:
            raise invalid("schedule.run_at", "must_be_in_future")
        return rule.run_at
    return Calendar.parse(rule.expression, rule.timezone).next(after)


async def validate_invocation(
    session: AsyncSession,
    runtime: Runtime,
    actor: Principal,
    scope: WorkspaceScope,
    invocation: ScheduleInvocation,
    authority: ExecutionAuthority,
) -> None:
    authorize(actor, scope, "run", authority=authority)
    revision, overrides = await run_revision(
        session,
        runtime,
        actor,
        scope,
        invocation.agent_id,
        invocation.agent_revision_id,
        invocation.options.overrides,
        authority=authority,
    )
    # Overrides already pass the complete configuration validator in run_revision.
    if overrides is None:
        await validate_config(
            session,
            actor,
            scope,
            revision.agent_id,
            revision.config,
            registry=runtime.registry,
            plugins=runtime.plugins,
            verb="run",
            authority=authority,
            held=revision.config,
        )
    if payload_size(invocation.payload.model_dump(mode="json")) > runtime.settings.control.inbox_bytes:
        raise invalid("invocation.payload", "inbox_bytes_exceeded")
    if len(invocation.memories) > runtime.settings.memory.mounts_per_thread:
        raise invalid("invocation.memories", "memory_mount_limit")
    for mount in invocation.memories:
        await resolve_memory(session, actor, scope, mount.memory_id, verb="run", authority=authority)


async def create(
    runtime: Runtime, actor: Principal, workspace_id: str, body: ScheduleCreate, *, request_key: str
) -> tuple[Schedule, bool]:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        authority = execution_authority(actor, scope)
        digest = request_digest("schedule.create", scope.workspace_id, body.model_dump(mode="json"))
        # Both the quota and replay arbitration belong to this workspace. No tentative Session is needed by a loser.
        await advisory_lock(session, "schedules", scope.workspace_id)
        found = await session.scalar(
            select(ScheduleRow).where(
                ScheduleRow.workspace_id == scope.workspace_id,
                ScheduleRow.created_by_id == actor.id,
                ScheduleRow.create_request_key == request_key,
            )
        )
        if found is not None:
            if found.create_request_digest != digest:
                raise conflict("request", request_key, IDEMPOTENCY_KEY_REUSED)
            return queries.view(found), False
        count = await session.scalar(
            select(func.count())
            .select_from(ScheduleRow)
            .where(
                ScheduleRow.workspace_id == scope.workspace_id,
                ScheduleRow.state != "archived",
            )
        )
        if (count or 0) >= runtime.settings.schedules.max_per_workspace:
            raise rate_limited(
                "Workspace Schedule limit reached",
                60,
                {"kind": "schedule_limit", "limit": runtime.settings.schedules.max_per_workspace},
            )
        await validate_invocation(session, runtime, actor, scope, body.invocation, authority)
        current = await now(session)
        future = first_fire(body.schedule, current)
        owner = new_session(scope.organization_id, scope.workspace_id, actor.id)
        owner.memory = SessionMemorySettings(enabled=False).model_dump(mode="json")
        session.add(owner)
        await session.flush()
        row = ScheduleRow(
            id=new_object_id("sch"),
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            session_id=owner.id,
            name=body.name,
            description=body.description,
            labels=dict(body.labels),
            state=body.state,
            pause_reason="manual" if body.state == "paused" else None,
            **time_values(body.schedule),
            max_lateness_seconds=body.max_lateness_seconds,
            invocation=body.invocation.model_dump(mode="json"),
            principal_id=actor.id,
            authority=authority.model_dump(mode="json"),
            created_by_id=actor.id,
            updated_by_id=actor.id,
            create_request_key=request_key,
            create_request_digest=digest,
            next_fire_at=future if body.state == "enabled" else None,
        )
        session.add(row)
        audit_row(session, actor, row, "create", {"session_id": owner.id})
        await session.flush()
        if row.state == "enabled":
            await webhooks.schedule_changed(session, runtime, row, current)
        return queries.view(row), True


async def update(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    schedule_id: str,
    body: ScheduleUpdate,
    *,
    if_match: str | None,
) -> Schedule:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        row = await queries.find(session, scope.workspace_id, schedule_id, lock=True)
        require_owner(row, actor)
        authority = ExecutionAuthority.model_validate(row.authority)
        authorize(actor, scope, "write", authority=authority)
        authorize(actor, scope, "run", authority=authority)
        require_match(if_match, row.id, row.version)
        require_editable(row)
        config = ScheduleConfig.model_validate(
            {
                **queries.view(row).model_dump(mode="json", include=set(ScheduleConfig.model_fields)),
                **body.model_dump(mode="json", exclude_unset=True),
            }
        )
        await validate_invocation(session, runtime, actor, scope, config.invocation, authority)
        current = await now(session)
        values = {
            "name": config.name,
            "description": config.description,
            "labels": dict(config.labels),
            **time_values(config.schedule),
            "max_lateness_seconds": config.max_lateness_seconds,
            "invocation": config.invocation.model_dump(mode="json"),
        }
        changed = [name for name, value in values.items() if getattr(row, name) != value]
        if not changed:
            return queries.view(row)
        timing_changed = bool(
            set(changed)
            & {"schedule_type", "run_at", "cron_expression", "timezone", "max_lateness_seconds", "invocation"}
        )
        if timing_changed:
            after = max(current, row.last_processed_fire_at) if row.last_processed_fire_at else current
            first_fire(config.schedule, after)
            await lifecycle.cancel_pending(session, runtime, row, current, "schedule_changed")
            await lifecycle.close_due(session, runtime, row, current, "schedule_changed")
        assign(row, values)
        if timing_changed:
            row.next_fire_at = lifecycle.next_fire(row, current) if row.state == "enabled" else None
        row.updated_by_id = actor.id
        audit_row(session, actor, row, "update", {"session_id": row.session_id, "fields": list(changed)})
        await session.flush()
        return queries.view(row)


async def transition(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    schedule_id: str,
    command: Literal["enable", "pause", "archive"],
    *,
    if_match: str | None,
) -> Schedule:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        row = await queries.find(session, scope.workspace_id, schedule_id, lock=True)
        authority = ExecutionAuthority.model_validate(row.authority)
        if command == "enable":
            require_owner(row, actor)
            authorize(actor, scope, "write", authority=authority)
            authorize(actor, scope, "run", authority=authority)
        require_match(if_match, row.id, row.version)
        if row.state == "archived" and command == "archive":
            return queries.view(row)
        if command != "archive":
            require_editable(row)
        current = await now(session)
        previous_state = (row.state, row.pause_reason)
        cancelled = None
        if command == "enable":
            await validate_invocation(
                session, runtime, actor, scope, ScheduleInvocation.model_validate(row.invocation), authority
            )
            if row.state == "enabled":
                return queries.view(row)
            after = max(current, row.last_processed_fire_at) if row.last_processed_fire_at else current
            row.next_fire_at = first_fire(lifecycle.rule(row), after)
            row.state, row.pause_reason = "enabled", None
        else:
            reason = "schedule_paused" if command == "pause" else "schedule_archived"
            cancelled = await lifecycle.cancel_pending(session, runtime, row, current, reason)
            await lifecycle.close_due(session, runtime, row, current, reason)
            row.next_fire_at = None
            row.state = "paused" if command == "pause" else "archived"
            row.pause_reason = "manual" if command == "pause" else None
            row.archived_at = current if command == "archive" else None
        row.updated_by_id = actor.id
        if previous_state != (row.state, row.pause_reason):
            await webhooks.schedule_changed(session, runtime, row, current)
        audit_row(session, actor, row, command, {"session_id": row.session_id, "cancelled_occurrence_id": cancelled})
        await session.flush()
        return queries.view(row)


async def preview(
    runtime: Runtime, actor: Principal, workspace_id: str, body: SchedulePreviewRequest
) -> SchedulePreview:
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "read")
    if isinstance(body.schedule, OnceSchedule):
        instant = body.schedule.run_at
        return SchedulePreview(items=[ScheduleTime(utc=instant, local=instant)] if instant > body.after else [])
    calendar = Calendar.parse(body.schedule.expression, body.schedule.timezone)
    current, items = body.after, []
    for _ in range(body.count):
        current = calendar.next(current)
        items.append(ScheduleTime(utc=current, local=current.astimezone(calendar.timezone)))
    return SchedulePreview(items=items)
