"""Session selection, authorized previews and counts for the workspace collection."""

from __future__ import annotations

from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import and_, false, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from a13n_service.agents.models import AgentRecord
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent_scoped_collection
from a13n_service.iam.authorization import AuthorizedAgentCollection
from a13n_service.interactions.access import authorize_interaction, configuration_visibility
from a13n_service.interactions.domain import RunStatus, SessionPurpose
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.labels import LabelFilterValues, Labels, label_predicates, parse_label_filters
from a13n_service.temporal import assume_utc


class _Resource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionFilters(_Resource):
    q: str | None = Field(default=None, max_length=72)
    agent_id: str | None = Field(default=None, max_length=72)
    status: tuple[RunStatus, ...] = Field(default=(), max_length=7)
    trigger_type: tuple[str, ...] = Field(default=(), max_length=16)
    updated_after: AwareDatetime | None = None
    updated_before: AwareDatetime | None = None
    label: LabelFilterValues = ()

    @property
    def labels(self) -> dict[str, str]:
        return parse_label_filters(self.label)

    @field_validator("q", "agent_id")
    @classmethod
    def trim_identifier(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("status", "trigger_type")
    @classmethod
    def canonical_choices(cls, values: tuple) -> tuple:
        if any(not value or len(value) > 256 for value in values):
            raise ValueError("Filter values must contain 1 to 256 characters.")
        return tuple(sorted(set(values)))

    @model_validator(mode="after")
    def ordered_dates(self) -> SessionFilters:
        if self.updated_after and self.updated_before and self.updated_after >= self.updated_before:
            raise ValueError("The start of the update range must precede its end.")
        return self

    @property
    def filters_run(self) -> bool:
        return bool(self.agent_id or self.status or self.trigger_type)


class SessionPreview(_Resource):
    thread_id: str
    run_id: str
    input_text: str | None = Field(max_length=256)
    output_text: str | None = Field(max_length=512)
    agent_name: str | None
    run_status: RunStatus
    trigger_type: str


class SessionResource(_Resource):
    purpose: SessionPurpose
    id: str
    workspace_id: str
    created_at: datetime
    updated_at: datetime
    labels: Labels
    preview: SessionPreview | None
    run_count: int | None = Field(ge=0)


class SessionCollection(_Resource):
    items: tuple[SessionResource, ...]
    next_cursor: str | None


async def collect_sessions(
    database: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    authorization: AuthorizedAgentCollection,
    filters: SessionFilters,
    boundary: tuple[datetime, str] | None,
    limit: int,
) -> tuple[SessionResource, ...]:
    threads = await _optional_authority(database, actor, workspace_id, WorkspaceAction.thread_read)
    runs = await _optional_authority(database, actor, workspace_id, WorkspaceAction.run_read)
    query = (
        select(SessionRecord)
        .where(
            SessionRecord.organization_id == authorization.workspace.organization_id,
            SessionRecord.workspace_id == workspace_id,
        )
        .order_by(SessionRecord.updated_at.desc(), SessionRecord.id.desc())
        .limit(limit + 1)
    )
    query = query.where(
        *label_predicates(
            SessionRecord.labels,
            filters.labels,
        )
    )
    ordinary = (
        true()
        if authorization.visible_agent_ids is None
        else (
            select(RunRecord.id)
            .where(
                RunRecord.organization_id == SessionRecord.organization_id,
                RunRecord.session_id == SessionRecord.id,
                RunRecord.agent_id.in_(authorization.visible_agent_ids),
            )
            .exists()
        )
    )
    query = query.where(
        or_(
            and_(SessionRecord.configuration_owner_user_id.is_(None), ordinary),
            await configuration_visibility(
                database,
                actor=actor,
                organization_id=authorization.workspace.organization_id,
                workspace_id=workspace_id,
                action=WorkspaceAction.session_read,
            ),
        )
    )
    if filters.q:
        matched_thread = (
            select(ThreadRecord.session_id)
            .outerjoin(
                RunRecord,
                and_(
                    RunRecord.organization_id == ThreadRecord.organization_id,
                    RunRecord.id == ThreadRecord.current_run_id,
                ),
            )
            .where(
                ThreadRecord.organization_id == authorization.workspace.organization_id,
                ThreadRecord.id == filters.q,
            )
        )
        if threads is None:
            query = query.where(SessionRecord.id == filters.q)
        else:
            if threads.visible_agent_ids is not None:
                matched_thread = matched_thread.where(RunRecord.agent_id.in_(threads.visible_agent_ids))
            query = query.where(or_(SessionRecord.id == filters.q, SessionRecord.id.in_(matched_thread)))
    if filters.updated_after:
        query = query.where(SessionRecord.updated_at >= filters.updated_after)
    if filters.updated_before:
        query = query.where(SessionRecord.updated_at < filters.updated_before)
    if filters.filters_run:
        if threads is None or runs is None:
            query = query.where(false())
        else:
            selected = _session_run_query(
                authorization.workspace.organization_id,
                threads.visible_agent_ids,
                runs.visible_agent_ids,
            ).with_only_columns(ThreadRecord.session_id)
            if filters.agent_id:
                selected = selected.where(RunRecord.agent_id == filters.agent_id)
            if filters.status:
                selected = selected.where(RunRecord.status.in_(filters.status))
            if filters.trigger_type:
                selected = selected.where(RunRecord.trigger_type.in_(filters.trigger_type))
            query = query.where(SessionRecord.id.in_(selected))
    if boundary is not None:
        updated_at, resource_id = boundary
        query = query.where(
            or_(
                SessionRecord.updated_at < updated_at,
                and_(SessionRecord.updated_at == updated_at, SessionRecord.id < resource_id),
            )
        )

    records = tuple((await database.scalars(query)).all())
    session_ids = tuple(item.id for item in records[:limit])
    previews = await _session_previews(
        database,
        actor=actor,
        workspace_id=workspace_id,
        organization_id=authorization.workspace.organization_id,
        session_ids=session_ids,
        threads=threads,
        runs=runs,
    )
    counts = await _session_run_counts(
        database, organization_id=authorization.workspace.organization_id, session_ids=session_ids, runs=runs
    )
    for record in records[:limit]:
        if record.configuration_owner_user_id is None:
            continue
        previews.pop(record.id, None)
        counts.pop(record.id, None)
        try:
            await authorize_interaction(
                database,
                actor=actor,
                workspace_id=workspace_id,
                session_id=record.id,
                agent_id=None,
                action=WorkspaceAction.run_read,
            )
        except AuthorizationError:
            continue
        counts[record.id] = (
            await database.scalar(select(func.count(RunRecord.id)).where(RunRecord.session_id == record.id)) or 0
        )
        try:
            await authorize_interaction(
                database,
                actor=actor,
                workspace_id=workspace_id,
                session_id=record.id,
                agent_id=None,
                action=WorkspaceAction.thread_read,
            )
        except AuthorizationError:
            continue
        latest = await database.scalar(
            select(RunRecord)
            .join(ThreadRecord, RunRecord.id == func.coalesce(ThreadRecord.current_run_id, ThreadRecord.head_run_id))
            .where(ThreadRecord.session_id == record.id)
            .order_by(ThreadRecord.updated_at.desc(), ThreadRecord.id.desc())
            .limit(1)
        )
        if latest is not None:
            previews[record.id] = SessionPreview(
                thread_id=latest.thread_id,
                run_id=latest.id,
                input_text=None if latest.input_text is None else latest.input_text[:256],
                output_text=None if latest.output_text is None else latest.output_text[:512],
                agent_name=None,
                run_status=RunStatus(latest.status),
                trigger_type=latest.trigger_type,
            )
    return tuple(_session(item, previews.get(item.id), counts.get(item.id)) for item in records)


async def _optional_authority(
    database: AsyncSession,
    actor: AuthenticatedActor,
    workspace_id: str,
    action: WorkspaceAction,
) -> AuthorizedAgentCollection | None:
    try:
        return await authorize_agent_scoped_collection(database, actor=actor, workspace_id=workspace_id, action=action)
    except AuthorizationError:
        return None


def _session_run_query(organization_id: str, thread_agents: frozenset[str] | None, run_agents: frozenset[str] | None):
    latest_thread = aliased(ThreadRecord, name="latest_thread")
    current_run = aliased(RunRecord, name="latest_thread_run")
    latest_id = (
        select(latest_thread.id)
        .outerjoin(
            current_run,
            and_(
                current_run.organization_id == latest_thread.organization_id,
                current_run.id == latest_thread.current_run_id,
            ),
        )
        .where(latest_thread.organization_id == organization_id, latest_thread.session_id == ThreadRecord.session_id)
        .order_by(latest_thread.updated_at.desc(), latest_thread.id.desc())
        .limit(1)
        .correlate(ThreadRecord)
    )
    if thread_agents is not None:
        latest_id = latest_id.where(current_run.agent_id.in_(thread_agents))
    query = (
        select(
            ThreadRecord.session_id,
            ThreadRecord.id.label("thread_id"),
            RunRecord.id.label("run_id"),
            func.substr(RunRecord.input_text, 1, 256),
            func.substr(RunRecord.output_text, 1, 512),
            RunRecord.agent_id,
            RunRecord.status,
            RunRecord.trigger_type,
        )
        .select_from(ThreadRecord)
        .join(
            RunRecord,
            and_(
                RunRecord.organization_id == ThreadRecord.organization_id,
                RunRecord.id == func.coalesce(ThreadRecord.current_run_id, ThreadRecord.head_run_id),
            ),
        )
        .where(
            ThreadRecord.organization_id == organization_id,
            ThreadRecord.id == latest_id.scalar_subquery(),
        )
    )
    if run_agents is not None:
        query = query.where(RunRecord.agent_id.in_(run_agents))
    return query.correlate(None)


async def _session_previews(
    database: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    organization_id: str,
    session_ids: tuple[str, ...],
    threads: AuthorizedAgentCollection | None,
    runs: AuthorizedAgentCollection | None,
) -> dict[str, SessionPreview]:
    if not session_ids or threads is None or runs is None:
        return {}
    query = _session_run_query(organization_id, threads.visible_agent_ids, runs.visible_agent_ids).where(
        ThreadRecord.session_id.in_(session_ids)
    )
    rows = (await database.execute(query)).tuples().all()
    names: dict[str, str] = {}
    try:
        agents = await authorize_agent_scoped_collection(
            database, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.agent_read
        )
    except AuthorizationError:
        pass
    else:
        agent_query = select(AgentRecord.id, AgentRecord.name).where(
            AgentRecord.organization_id == organization_id,
            AgentRecord.workspace_id == workspace_id,
            AgentRecord.id.in_({row[5] for row in rows}),
            AgentRecord.system_purpose.is_(None),
        )
        if agents.visible_agent_ids is not None:
            agent_query = agent_query.where(AgentRecord.id.in_(agents.visible_agent_ids))
        names = dict((await database.execute(agent_query)).tuples().all())
    return {
        session_id: SessionPreview(
            thread_id=thread_id,
            run_id=run_id,
            input_text=input_text,
            output_text=output_text,
            agent_name=names.get(agent_id),
            run_status=RunStatus(status),
            trigger_type=trigger_type,
        )
        for session_id, thread_id, run_id, input_text, output_text, agent_id, status, trigger_type in rows
    }


async def _session_run_counts(
    database: AsyncSession,
    *,
    organization_id: str,
    session_ids: tuple[str, ...],
    runs: AuthorizedAgentCollection | None,
) -> dict[str, int]:
    if not session_ids or runs is None:
        return {}
    query = (
        select(RunRecord.session_id, func.count(RunRecord.id))
        .where(RunRecord.organization_id == organization_id, RunRecord.session_id.in_(session_ids))
        .group_by(RunRecord.session_id)
    )
    if runs.visible_agent_ids is not None:
        query = query.where(RunRecord.agent_id.in_(runs.visible_agent_ids))
    counts = dict.fromkeys(session_ids, 0)
    counts.update((await database.execute(query)).tuples().all())
    return counts


def _session(record: SessionRecord, preview: SessionPreview | None, run_count: int | None) -> SessionResource:
    return SessionResource(
        purpose=SessionPurpose(record.purpose),
        id=record.id,
        workspace_id=record.workspace_id,
        created_at=assume_utc(record.created_at),
        updated_at=assume_utc(record.updated_at),
        labels=record.labels,
        preview=preview,
        run_count=run_count,
    )
