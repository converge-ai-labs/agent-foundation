"""Authorized interaction label reads and atomic metadata updates."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.etags import etag_matches
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_workspace
from a13n_service.iam.audit import security_audit_record
from a13n_service.ids import new_object_id
from a13n_service.interactions.access import authorize_interaction
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.labels import Labels, LabelsBody, labels_etag
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import next_updated_at, utc_now

from .queries import NativeQueryError, _authorize_agent, _authorize_collection, _load_run, _load_thread, _not_found


class InteractionLabels:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get_session_labels(self, *, actor: AuthenticatedActor, session_id: str) -> tuple[LabelsBody, str]:
        async with short_session(self._sessions) as database:
            authorization = await _authorize_collection(
                database,
                actor=actor,
                workspace_id=actor.workspace_id,
                action=WorkspaceAction.session_read,
            )
            row = await database.scalar(
                select(SessionRecord).where(
                    SessionRecord.id == session_id,
                    SessionRecord.organization_id == authorization.workspace.organization_id,
                    SessionRecord.workspace_id == actor.workspace_id,
                )
            )
            if row is None:
                raise _not_found()
            if row.configuration_owner_user_id is not None:
                await _authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.workspace_id,
                    session_id=row.id,
                    agent_id=None,
                    action=WorkspaceAction.session_read,
                )
            elif authorization.visible_agent_ids is not None:
                visible = await database.scalar(
                    select(RunRecord.id)
                    .where(
                        RunRecord.organization_id == row.organization_id,
                        RunRecord.session_id == row.id,
                        RunRecord.agent_id.in_(authorization.visible_agent_ids),
                    )
                    .limit(1)
                )
                if visible is None:
                    raise _not_found()
            return _label_result(row.id, row.labels)

    async def get_thread_labels(self, *, actor: AuthenticatedActor, thread_id: str) -> tuple[LabelsBody, str]:
        async with short_session(self._sessions) as database:
            thread, run, workspace_id = await _load_thread(database, actor=actor, thread_id=thread_id)
            await _authorize_agent(
                database,
                actor=actor,
                workspace_id=workspace_id,
                agent_id=run.agent_id if run else None,
                session_id=thread.session_id,
                action=WorkspaceAction.thread_read,
            )
            return _label_result(thread.id, thread.labels)

    async def get_run_labels(self, *, actor: AuthenticatedActor, run_id: str) -> tuple[LabelsBody, str]:
        async with short_session(self._sessions) as database:
            run = await _load_run(database, actor=actor, run_id=run_id)
            await _authorize_agent(
                database,
                actor=actor,
                workspace_id=actor.workspace_id,
                agent_id=run.agent_id,
                session_id=run.session_id,
                action=WorkspaceAction.run_read,
            )
            return _label_result(run.id, run.labels)

    async def put_session_labels(
        self, *, actor: AuthenticatedActor, session_id: str, body: LabelsBody, if_match: str
    ) -> tuple[LabelsBody, str]:
        async with transaction(self._sessions) as database:
            await _authorize_label_update(database, actor, WorkspaceAction.session_labels_update)
            row = await database.scalar(
                select(SessionRecord)
                .where(SessionRecord.id == session_id, SessionRecord.workspace_id == actor.workspace_id)
                .with_for_update(of=SessionRecord)
            )
            if row is not None:
                await authorize_interaction(
                    database,
                    actor=actor,
                    workspace_id=actor.workspace_id,
                    session_id=row.id,
                    agent_id=None,
                    action=WorkspaceAction.session_labels_update,
                )
            return _replace_labels(database, actor, row, session_id, "session", body, if_match)

    async def put_thread_labels(
        self, *, actor: AuthenticatedActor, thread_id: str, body: LabelsBody, if_match: str
    ) -> tuple[LabelsBody, str]:
        async with transaction(self._sessions) as database:
            await _authorize_label_update(database, actor, WorkspaceAction.thread_labels_update)
            row = await database.scalar(
                select(ThreadRecord)
                .join(SessionRecord, SessionRecord.id == ThreadRecord.session_id)
                .where(ThreadRecord.id == thread_id, SessionRecord.workspace_id == actor.workspace_id)
                .with_for_update(of=ThreadRecord)
            )
            if row is not None:
                await authorize_interaction(
                    database,
                    actor=actor,
                    workspace_id=actor.workspace_id,
                    session_id=row.session_id,
                    agent_id=None,
                    action=WorkspaceAction.thread_labels_update,
                )
            return _replace_labels(database, actor, row, thread_id, "thread", body, if_match)

    async def put_run_labels(
        self, *, actor: AuthenticatedActor, run_id: str, body: LabelsBody, if_match: str
    ) -> tuple[LabelsBody, str]:
        async with transaction(self._sessions) as database:
            await _authorize_label_update(database, actor, WorkspaceAction.run_labels_update)
            row = await database.scalar(
                select(RunRecord)
                .join(SessionRecord, SessionRecord.id == RunRecord.session_id)
                .where(RunRecord.id == run_id, SessionRecord.workspace_id == actor.workspace_id)
                .with_for_update(of=RunRecord)
            )
            if row is not None:
                await authorize_interaction(
                    database,
                    actor=actor,
                    workspace_id=actor.workspace_id,
                    session_id=row.session_id,
                    agent_id=row.agent_id,
                    action=WorkspaceAction.run_labels_update,
                )
            return _replace_labels(database, actor, row, run_id, "run", body, if_match)


def _label_result(resource_id: str, labels: Labels) -> tuple[LabelsBody, str]:
    return LabelsBody(labels=labels), labels_etag(resource_id, labels)


def _replace_labels(
    database: AsyncSession,
    actor: AuthenticatedActor,
    row: SessionRecord | ThreadRecord | RunRecord | None,
    resource_id: str,
    kind: str,
    body: LabelsBody,
    if_match: str,
) -> tuple[LabelsBody, str]:
    if row is None:
        raise _not_found()
    current = labels_etag(resource_id, row.labels)
    if not etag_matches(if_match, current):
        raise NativeQueryError(
            "labels_etag_mismatch",
            "Labels changed since they were read.",
            category=ErrorCategory.stale_version,
            details={"current_etag": current},
        )
    if row.labels != body.labels:
        row.labels = dict(body.labels)
        row.updated_at = next_updated_at(row.updated_at, utc_now())
        database.add(
            security_audit_record(
                audit_id=new_object_id("audit"),
                actor=actor,
                organization_id=row.organization_id,
                workspace_id=actor.workspace_id,
                action=f"{kind}.labels.update",
                resource_type=kind,
                resource_id=resource_id,
                outcome="success",
                occurred_at=row.updated_at,
                details=None,
            )
        )
    return _label_result(resource_id, row.labels)


async def _authorize_label_update(database: AsyncSession, actor: AuthenticatedActor, action: WorkspaceAction) -> None:
    try:
        await authorize_workspace(database, actor=actor, workspace_id=actor.workspace_id, action=action)
    except AuthorizationError as error:
        raise _not_found() from error
