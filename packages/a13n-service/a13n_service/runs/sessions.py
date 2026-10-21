"""Caller-owned Session identity and listings with bounded execution previews."""

import re
from collections.abc import Sequence

from pydantic import JsonValue
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import conflict, not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import OBJECT_ID_PATTERN, new_object_id
from a13n_service.infra.labels import label_filter
from a13n_service.resources.agents.tables import AgentRow
from a13n_service.runs.memories.session_memory import configure
from a13n_service.runs.schedules.tables import ScheduleRow
from a13n_service.runs.schemas import (
    SessionCreate,
    SessionPage,
    SessionPreview,
    SessionQuery,
    SessionUpdate,
    SessionView,
)
from a13n_service.runs.search import predicate
from a13n_service.runs.tables import InboxEntryRow, RunRow, SessionRow, ThreadRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal

PREVIEW_INPUT = 256
PREVIEW_OUTPUT = 512


def _input_text(payload: dict[str, JsonValue] | None) -> str | None:
    content = payload.get("content") if payload is not None else None
    if not isinstance(content, list):
        return None
    texts = [text for part in content if isinstance(part, dict) and isinstance(text := part.get("text"), str)]
    return "\n".join(texts)[:PREVIEW_INPUT] if texts else None


def new_session(
    organization_id: str, workspace_id: str, created_by_id: str, details: SessionCreate | None = None
) -> SessionRow:
    details = details or SessionCreate()
    return SessionRow(
        id=new_object_id("sess"),
        organization_id=organization_id,
        workspace_id=workspace_id,
        title=details.title,
        origin=details.origin.model_dump(mode="json") if details.origin is not None else None,
        labels=dict(details.labels),
        created_by_id=created_by_id,
    )


async def _views(session: AsyncSession, rows: Sequence[SessionRow]) -> list[SessionView]:
    """Views with the latest run's preview and each session's run count, and Schedule association, batched per page."""
    ids = [row.id for row in rows]
    schedules = dict(
        (await session.execute(select(ScheduleRow.session_id, ScheduleRow.id).where(ScheduleRow.session_id.in_(ids))))
        .tuples()
        .all()
    )
    previews: dict[str, SessionPreview] = {}
    for session_id, run, agent_name, payload in (
        await session.execute(
            select(SessionRow.id, RunRow, AgentRow.name, InboxEntryRow.payload)
            .join(RunRow, RunRow.id == SessionRow.last_run_id)
            .join(AgentRow, AgentRow.id == RunRow.agent_id)
            .outerjoin(InboxEntryRow, InboxEntryRow.id == RunRow.source_entry_id)
            .where(SessionRow.id.in_(ids))
        )
    ).tuples():
        previews[session_id] = SessionPreview.model_validate(
            {
                "run_id": run.id,
                "thread_id": run.thread_id,
                "agent_id": run.agent_id,
                "agent_name": agent_name,
                "status": run.status,
                "trigger": run.trigger,
                "input_text": _input_text(payload),
                "output_text": run.output[:PREVIEW_OUTPUT] if isinstance(run.output, str) else None,
            }
        )
    counts = dict(
        (
            await session.execute(
                select(RunRow.session_id, func.count()).where(RunRow.session_id.in_(ids)).group_by(RunRow.session_id)
            )
        )
        .tuples()
        .all()
    )
    return [
        SessionView.model_validate(row).model_copy(
            update={
                "preview": previews.get(row.id),
                "run_count": counts.get(row.id, 0),
                "schedule_id": schedules.get(row.id),
            }
        )
        for row in rows
    ]


def require_conversation(row: SessionRow) -> None:
    if row.purpose != "conversation":
        raise conflict("session", row.id, "managed_organization")


async def find_session(session: AsyncSession, workspace_id: str, session_id: str, *, lock: bool = False) -> SessionRow:
    query = select(SessionRow).where(SessionRow.workspace_id == workspace_id, SessionRow.id == session_id)
    found = await session.scalar(query.with_for_update(key_share=True) if lock else query)
    if found is None:
        raise not_found("session", session_id)
    return found


async def list_sessions(storage: Storage, actor: Principal, workspace_id: str, filters: SessionQuery) -> SessionPage:
    """Most recently updated first; identity search and retained text share the authorized workspace."""
    match = predicate(RunRow.search_vector, filters.text_query)
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        query = select(SessionRow).where(
            SessionRow.workspace_id == scope.workspace_id,
            SessionRow.purpose == "conversation",
            label_filter(SessionRow.labels, list(filters.label)),
        )
        if filters.q and (needle := filters.q.strip()):
            by_thread = select(ThreadRow.session_id).where(
                ThreadRow.workspace_id == scope.workspace_id, ThreadRow.id == needle
            )
            matches = [SessionRow.id == needle, SessionRow.id.in_(by_thread)]
            exact_id = needle.startswith(("sess_", "thread_")) and re.fullmatch(OBJECT_ID_PATTERN, needle)
            if not exact_id:
                label_values = func.jsonb_each_text(SessionRow.labels).table_valued("key", "value")
                matches.extend(
                    [
                        SessionRow.title.icontains(needle, autoescape=True),
                        *[
                            SessionRow.origin[field].astext.icontains(needle, autoescape=True)
                            for field in ("application", "name", "external_id")
                        ],
                        select(1)
                        .select_from(label_values)
                        .where(label_values.c.value.icontains(needle, autoescape=True))
                        .exists(),
                    ]
                )
                text_match = predicate(RunRow.search_vector, needle)
                if text_match is not None:
                    matches.append(
                        SessionRow.id.in_(
                            select(RunRow.session_id).where(RunRow.workspace_id == scope.workspace_id, text_match)
                        )
                    )
            query = query.where(or_(*matches))
        if filters.application:
            query = query.where(SessionRow.origin["application"].astext == filters.application)
        if filters.updated_after:
            query = query.where(SessionRow.updated_at >= filters.updated_after)
        if filters.updated_before:
            query = query.where(SessionRow.updated_at < filters.updated_before)
        if filters.agent_id or filters.status or filters.trigger:
            query = query.join(RunRow, RunRow.id == SessionRow.last_run_id)
            if filters.agent_id:
                query = query.where(RunRow.agent_id == filters.agent_id)
            if filters.status:
                query = query.where(RunRow.status.in_(filters.status))
            if filters.trigger:
                query = query.where(RunRow.trigger.in_(filters.trigger))
        if match is not None:
            query = query.where(
                SessionRow.id.in_(select(RunRow.session_id).where(RunRow.workspace_id == scope.workspace_id, match))
            )
        rows, next_cursor = await cursors.keyset_page(
            session,
            query,
            (SessionRow.updated_at, SessionRow.id),
            kind="sessions",
            owner=cursors.query_owner(scope.workspace_id, filters.model_dump(exclude={"limit", "cursor"})),
            cursor=filters.cursor,
            limit=filters.limit,
            newest_first=True,
        )
        return SessionPage(items=await _views(session, rows), next_cursor=next_cursor)


async def get_session(storage: Storage, actor: Principal, workspace_id: str, session_id: str) -> SessionView:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return (await _views(session, [await find_session(session, scope.workspace_id, session_id)]))[0]


async def create_session(storage: Storage, actor: Principal, workspace_id: str, body: SessionCreate) -> SessionView:
    """An empty session; threads join it through `POST /threads` with its ID."""
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        row = new_session(scope.organization_id, scope.workspace_id, actor.id, body)
        session.add(row)
        await session.flush()
        if body.memory is not None:
            await configure(session, row, body.memory)
            await session.flush()
        return (await _views(session, [row]))[0]


async def update_session(
    storage: Storage, actor: Principal, workspace_id: str, session_id: str, body: SessionUpdate, *, if_match: str | None
) -> SessionView:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        row = await find_session(session, scope.workspace_id, session_id, lock=True)
        require_match(if_match, row.id, row.version)
        if "title" in body.model_fields_set:
            row.title = body.title
        if "origin" in body.model_fields_set:
            row.origin = body.origin.model_dump(mode="json") if body.origin is not None else None
        if body.labels is not None:
            row.labels = dict(body.labels)
        if body.memory is not None:
            require_conversation(row)
            if await session.scalar(select(ScheduleRow.id).where(ScheduleRow.session_id == row.id)) is not None:
                raise conflict("session", row.id, "schedule_managed_memory")
            await configure(session, row, body.memory)
        await session.flush()
        return (await _views(session, [row]))[0]
