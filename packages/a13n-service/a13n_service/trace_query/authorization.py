"""Authorize backend trace correlations against current Service Run ownership."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import and_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_agent_scoped_collection,
)
from a13n_service.interactions.access import configuration_visibility
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, SessionRecord
from a13n_service.storage import short_session

from .domain import TraceCorrelation
from .errors import TraceQueryError
from .service import AuthorizedRunAttempt, TraceQueryScope


class RunTraceAccessAuthorizer:
    """Reuse Run and trace IAM grants, with no session retained across backend I/O."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def resolve_scope(self, *, actor: AuthenticatedActor, workspace_id: str) -> TraceQueryScope:
        async with short_session(self._sessions) as database:
            try:
                authorization = await authorize_agent_scoped_collection(
                    database, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.trace_read
                )
            except AuthorizationError as error:
                raise TraceQueryError(
                    "trace_not_found", "The Trace was not found.", category=ErrorCategory.not_found
                ) from error
            return TraceQueryScope(authorization.workspace.organization_id, authorization.workspace.workspace_id)

    async def authorize_run_attempts(
        self,
        *,
        actor: AuthenticatedActor,
        scope: TraceQueryScope,
        correlations: Sequence[TraceCorrelation],
    ) -> Mapping[str, AuthorizedRunAttempt]:
        candidates = {
            item.run_attempt_id: item
            for item in correlations
            if item.organization_id == scope.organization_id and item.workspace_id == scope.workspace_id
        }
        async with short_session(self._sessions) as database:
            # Reload grants and credential eligibility after the backend read.
            try:
                trace_access = await authorize_agent_scoped_collection(
                    database, actor=actor, workspace_id=scope.workspace_id, action=WorkspaceAction.trace_read
                )
                run_access = await authorize_agent_scoped_collection(
                    database, actor=actor, workspace_id=scope.workspace_id, action=WorkspaceAction.run_read
                )
            except AuthorizationError:
                return {}
            if trace_access.workspace.organization_id != scope.organization_id or not candidates:
                return {}
            query = (
                select(
                    RunAttemptRecord.id,
                    RunRecord.id.label("run_id"),
                    RunRecord.session_id,
                    RunRecord.thread_id,
                    RunRecord.agent_id,
                )
                .join(
                    RunRecord,
                    (RunRecord.organization_id == RunAttemptRecord.organization_id)
                    & (RunRecord.id == RunAttemptRecord.run_id),
                )
                .join(
                    SessionRecord,
                    (SessionRecord.organization_id == RunRecord.organization_id)
                    & (SessionRecord.id == RunRecord.session_id),
                )
                .where(
                    RunAttemptRecord.organization_id == scope.organization_id,
                    RunAttemptRecord.id.in_(candidates),
                    SessionRecord.workspace_id == scope.workspace_id,
                )
            )
            for access, action in ((trace_access, WorkspaceAction.trace_read), (run_access, WorkspaceAction.run_read)):
                ordinary = (
                    true() if access.visible_agent_ids is None else RunRecord.agent_id.in_(access.visible_agent_ids)
                )
                query = query.where(
                    or_(
                        and_(SessionRecord.configuration_owner_user_id.is_(None), ordinary),
                        await configuration_visibility(
                            database,
                            actor=actor,
                            organization_id=scope.organization_id,
                            workspace_id=scope.workspace_id,
                            action=action,
                        ),
                    )
                )
            authorized = {}
            for row in await database.execute(query):
                expected = TraceCorrelation(
                    organization_id=scope.organization_id,
                    workspace_id=scope.workspace_id,
                    session_id=row.session_id,
                    thread_id=row.thread_id,
                    run_id=row.run_id,
                    run_attempt_id=row.id,
                    agent_id=row.agent_id,
                )
                if candidates[row.id] != expected:
                    continue
                authorized[row.id] = AuthorizedRunAttempt(row.id)
            return authorized
